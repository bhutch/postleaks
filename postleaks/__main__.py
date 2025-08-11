#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import math
import os
import platform
import sys
import time

# Try to import curl_cffi first, fall back to requests if not available
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
    print("Using curl_cffi for HTTP requests")
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False
    print("Warning: curl_cffi not available, falling back to standard requests library")

import whispers

# Constants
POSTMAN_HOST = "https://www.postman.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HEADERS = {"User-Agent": USER_AGENT}
REQUEST_INFO_INTERESTING_DATA = ["id", "url", "method", "auth", "queryParams", "description", "name", "events", "data", "headerData"]

# Colors
BLUE = '\033[94m'
ORANGE = '\033[93m'
NOCOLOR = '\033[0m'

def create_session(impersonate_browser=None):
    """Create a requests session with optional browser impersonation"""
    if CURL_CFFI_AVAILABLE and impersonate_browser:
        try:
            return requests.Session(impersonate=impersonate_browser)
        except Exception as e:
            print(f"Warning: Failed to create session with impersonation '{impersonate_browser}': {e}")
            print("Falling back to regular session")
    
    return requests.Session()

def main():
    parser = argparse.ArgumentParser(description='Postleaks 🚀💧 Search for sensitive data in Postman public library.')
    parser.add_argument('-k', '--keyword', help='Keyword (Domain, company, etc.)')
    parser.add_argument('-kf', '--keyword-file', help='File containing keywords (one per line)')
    parser.add_argument('--extend-workspaces', action='store_true', help='Extend search to Postman workspaces linked to found requests (warning: request consuming and risk of false positive)')
    parser.add_argument('--strict', action='store_true', help='Only include results where keywords are in the URL (warning: could miss some results where the final URL is a variable)')
    parser.add_argument('--include', help='URL should match this string')
    parser.add_argument('--exclude', help='URL should not match this string')
    parser.add_argument('--raw', action='store_true', help='Display raw filtered results as JSON')
    parser.add_argument('--output', default=f"results_{int(time.time())}", help='Store JSON in specific output folder (Default: results_<TIMESTAMP>)')
    parser.add_argument('--impersonate-browser', default='chrome136', 
                       help='Browser fingerprint to impersonate with curl_cffi (Default: chrome136). Supported: chrome99, chrome100, chrome101, chrome104, chrome107, chrome110, chrome116, chrome119, chrome120, chrome123, chrome124, chrome126, chrome127, chrome131, chrome136, edge99, edge101, edge122, edge127, safari15_3, safari15_5, safari17_0, safari17_2_1, safari18_0')
    
    args = parser.parse_args()
    
    if not args.keyword and not args.keyword_file:
        parser.error("Either --keyword or --keyword-file must be specified")
    
    keywords = []
    if args.keyword:
        keywords.append(args.keyword)
    
    if args.keyword_file:
        try:
            with open(args.keyword_file, 'r') as f:
                keywords.extend([line.strip() for line in f if line.strip()])
        except FileNotFoundError:
            fail(f"Keyword file '{args.keyword_file}' not found", True)
    
    for keyword in keywords:
        print(f"{BLUE}[*] Searching for keyword: {keyword}{NOCOLOR}")
        search(keyword, args.extend_workspaces, args.include, args.exclude, args.raw, args.strict, args.output, args.impersonate_browser)

def search(keyword: str, extend_workspaces: bool, include_match: str, exclude_match: str, raw: bool, strict: bool, output: str, impersonate_browser: str):
    ids = search_requests_ids(keyword, impersonate_browser)
    
    if extend_workspaces:
        workspace_ids = set()
        for id_item in ids:
            for request_id, workspace_list in id_item.items():
                workspace_ids.update(workspace_list)
        
        additional_request_ids = search_request_ids_for_workspaces_id(workspace_ids, impersonate_browser)
        ids.extend([{req_id: []} for req_id in additional_request_ids])
    
    # Extract just the request IDs
    request_ids = set()
    for id_item in ids:
        request_ids.update(id_item.keys())
    
    search_request_info_for_request_ids(request_ids, include_match, exclude_match, raw, strict, keyword, output, impersonate_browser)

def display(id: str, request_info: any, raw: bool):
    if raw:
        print(json.dumps(request_info, indent=2))
        return
    
    print(f"{BLUE}[+] (ID:{id}) {request_info.get('method', 'UNKNOWN')}: '{request_info.get('url', 'NO_URL')}'{NOCOLOR}")
    
    if request_info.get("headerData"):
        print(" - Headers: ", end='')
        for d in request_info["headerData"]:
            if d.get("key") and d.get("value"):
                print(f"[{d['key']}='{d['value']}']", end='')
    
    if request_info.get("data"):
        print("\n - Misc. data items: ", end='')
        for data in request_info["data"]:
            if isinstance(data, dict) and data.get("key") and data.get("value"):
                print(f"[{data['key']}='{data['value']}']", end='')
            elif isinstance(data, str) and data.startswith("["):
                try:
                    tmp = json.loads(data)
                    for d in tmp:
                        if d.get('key'):
                            print(f"[{d['key']}='{d.get('value', '')}']", end='')
                except json.JSONDecodeError:
                    pass
    
    if request_info.get("queryParams"):
        print("\n - Query parameters: ", end='')
        for d in request_info["queryParams"]:
            if d.get("key") and d.get("value"):
                print(f"[{d['key']}='{d['value']}']", end='')
    
    print(f"{NOCOLOR}")

def search_request_info_for_request_ids(ids: set, include_match:str, exclude_match:str, raw: bool, strict: bool, keyword:str, output: str, impersonate_browser: str):
    print(BLUE+"[*] Search for requests info in collection of requests"+NOCOLOR)

    os.makedirs(output, exist_ok=True)

    GET_REQUEST_ENDPOINT="/_api/request/"

    request_infos = []

    session = create_session(impersonate_browser)
    for id in ids:
        response = session.get(POSTMAN_HOST+GET_REQUEST_ENDPOINT+str(id), headers=HEADERS)
        if (response.status_code != 200):
            # Request details not found - Skip
            continue
        
        request_info = {}

        if "data" in response.json():
            data = response.json()["data"]

            try:
                for key, value in data.items():
                    if key in REQUEST_INFO_INTERESTING_DATA:
                        # URL filtering
                        if key == "url" and value is not None and len(value) > 0:
                            if (include_match is not None or exclude_match is not None):
                                if (include_match is not None and include_match.lower() not in value.lower()):
                                    raise StopIteration
                                if (exclude_match is not None and exclude_match.lower() in value.lower()):
                                    raise StopIteration
                            if strict:
                                if (keyword.lower() not in value.lower()):
                                    raise StopIteration
                        request_info[key] = value
            except StopIteration:
                continue
            else:
                if "url" in request_info:
                    # Override the id field with the full ID (including prefix)
                    request_info["id"] = str(id)
                    request_infos.append(request_info)
                    display(str(id), request_info, raw)
                    f = store(request_info, output)
                    identify_secrets(f)
        
    return request_infos

def identify_secrets(file_path: any):
    config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
    if (platform.system() == 'Windows'):
        config_path = config_path.replace("\\","\\\\")
    secrets_raw = list(whispers.secrets(f"-c {config_path} {file_path}"))
    if (len(secrets_raw) > 0):
        secrets=list(set(s.key+" = "+s.value for s in secrets_raw))
        for secret in secrets:
            print(ORANGE+" > Potential secret found: " + secret + NOCOLOR)

def store(request_info: any, output: str):
    file_path = output + "/" + request_info["id"] + ".json"
    json_string = json.dumps(request_info, indent=2)
    with open(file_path, 'w') as file:
        file.write(json_string)
    return file_path

def search_request_ids_for_workspaces_id(ids: set, impersonate_browser: str):
    print(BLUE+"[*] Looking for requests IDs in collection of workspaces"+NOCOLOR)

    LIST_COLLECTION_ENDPOINT="/_api/list/collection"

    request_ids = set()

    session = create_session(impersonate_browser)
    for id in ids:
        response = session.post(POSTMAN_HOST+LIST_COLLECTION_ENDPOINT+"?workspace="+str(id), headers=HEADERS)
        if (response.status_code == 429):
            fail("Rate-limiting reached. Wait for 60 seconds before continuing ...")
            time.sleep(60)
            response = session.post(POSTMAN_HOST+LIST_COLLECTION_ENDPOINT+"?workspace="+str(id), headers=HEADERS)
        if (response.status_code != 200):
            fail("Error in [search_request_ids_for_workspaces_id] on returned results from Postman.com.")
            continue
        new_request_ids = parse_search_requests_from_workspace_response(response)
        if new_request_ids is not None:
            request_ids = request_ids.union(new_request_ids)

    return request_ids

def parse_search_requests_from_workspace_response(list_collection_response):
    json = list_collection_response.json()
    if "data" in json:
        data = json["data"]
        
        request_ids = set()
        for d in data:
            requests_raw = d["requests"]
            for r in requests_raw:
                request_ids.add(r["id"])

        return request_ids

def search_requests_ids(keyword: str, impersonate_browser: str):
    print(BLUE+"[*] Searching for requests IDs"+NOCOLOR)

    # https://www.postman.com/_api/ws/proxy limitation on results (<= 25)
    MAX_SEARCH_RESULTS = 25
    # https://www.postman.com/_api/ws/proxy limitation on offset (<= 200)
    MAX_OFFSET = 200
    GLOBAL_SEARCH_ENDPOINT="/_api/ws/proxy"

    session = create_session(impersonate_browser)
    response = session.post(POSTMAN_HOST+GLOBAL_SEARCH_ENDPOINT, json=format_search_request_body(keyword, 0, MAX_SEARCH_RESULTS), headers=HEADERS)
    if (response.status_code != 200):
        fail("Error in [search_requests_ids] on returned results from Postman.com.", True)
    count = response.json()["meta"]["total"]["request"]
    
    ids = parse_search_response(response)

    if count > MAX_SEARCH_RESULTS:
        max_requests = math.trunc(count / MAX_SEARCH_RESULTS)
        for i in range(1, max_requests+1):
            offset = i*MAX_SEARCH_RESULTS
            
            if offset > MAX_OFFSET:
                break
            r = session.post(POSTMAN_HOST+GLOBAL_SEARCH_ENDPOINT, json=format_search_request_body(keyword, offset, MAX_SEARCH_RESULTS), headers=HEADERS)
            if (r.status_code != 200):
                fail("Error in [search_requests_ids](loop) on returned results from Postman.com.")
                continue
            parsed = parse_search_response(r)
            ids.extend(parsed)
    return ids

def parse_search_response(search_response):

    json = search_response.json()
    
    if "data" not in json:
        fail("No data found", True)
    
    data = json["data"]

    # List composed of {"<requestId>":["workspaceId", ...]}
    ids = []
    for d in data:
        
        request_item = {}

        request_id = d["document"]["id"]
        workspaces_raw = d["document"]["workspaces"]
        workspace_ids = []
        for w in workspaces_raw:
            workspace_ids.append(w["id"])
        
        request_item[request_id] = workspace_ids

        ids.append(request_item)
        
    return ids

def format_search_request_body(keyword: str, offset: int, size: int):
    return {
        "service":"search",
        "method":"POST",
        "path":"/search-all",
        "body":{
            "queryIndices":["runtime.request"],
            "queryText": keyword,
            "size": size,
            "from": offset,
            "requestOrigin":"srp",
            "mergeEntities":"true",
            "nonNestedRequests":"true"
            }
        }

def fail(msg, exit=False):
    print(ORANGE+"[-] Error: "+msg+NOCOLOR)
    if exit:
        sys.exit()

if __name__ == '__main__':
    main()
