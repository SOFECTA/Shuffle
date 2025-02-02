import os
import copy
import sys
import re
import time 
import json
import logging
import requests
import urllib.parse
import http.client
import urllib3
import hashlib
from liquid import Liquid
import liquid
import zipfile
from io import BytesIO

class AppBase:
    __version__ = None
    app_name = None

    def __init__(self, redis=None, logger=None, console_logger=None):#, docker_client=None):
        self.logger = logger if logger is not None else logging.getLogger("AppBaseLogger")
        self.redis=redis
        self.console_logger = logger if logger is not None else logging.getLogger("AppBaseLogger")

        # apikey is for the user / org
        # authorization is for the specific workflow

        self.url = os.getenv("CALLBACK_URL", "https://shuffler.io")
        self.base_url = os.getenv("BASE_URL", "https://shuffler.io")
        self.action = os.getenv("ACTION", "")
        self.original_action = os.getenv("ACTION", "")
        self.authorization = os.getenv("AUTHORIZATION", "")
        self.current_execution_id = os.getenv("EXECUTIONID", "")
        self.full_execution = os.getenv("FULL_EXECUTION", "") 
        self.start_time = int(time.time())
        self.result_wrapper_count = 0

        self.action_result = {
            "action": self.action,
            "authorization": self.authorization,
            "execution_id": self.current_execution_id,
            "result": f"",
            "started_at": self.start_time,
            "status": "",
            "completed_at": int(time.time()),
        }

        if isinstance(self.action, str):
            try:
                self.action = json.loads(self.action)
                self.original_action = json.loads(self.action)
            except:
                self.logger.info("[WARNING] Failed parsing action as JSON")

        if len(self.base_url) == 0:
            self.base_url = self.url

    # FIXME: Add more info like logs in here.
    # Docker logs: https://forums.docker.com/t/docker-logs-inside-the-docker-container/68190/2
    def send_result(self, action_result, headers, stream_path):
        if action_result["status"] == "EXECUTING":
            action_result["status"] = "FAILURE"

        self.logger.info(f"""[DEBUG] Inside Send result with status {action_result["status"]}""")

        # FIXME: Add cleanup of parameters to not send to frontend here
        params = {}
        #action = action_result["action"]
        #try:
        #    for item in action["authentication"]:
        #        for action["parameters"]
        #        self.logger.info("AUTH: ", key, value)
        #        params[item["key"]] = item["value"]
       #except KeyError:
        #    self.logger.info("No authentication specified!")
        #    pass

        # I wonder if this actually works 
        self.logger.info(f"[DEBUG] Before last stream result")
        url = "%s%s" % (self.base_url, stream_path)
        #self.logger.info("[INFO] URL (URL): %s" % url)
        try:
            ret = requests.post(url, headers=headers, json=action_result)
            #self.logger.info(f"[DEBUG] Result: {ret.status_code}")
            #if ret.status_code != 200:
            #    self.logger.info(f"[DEBUG] Shuffle Response: {ret.text}")
        
            self.logger.info(f"[DEBUG] Successful request: Status= {ret.status_code} & Response= {ret.text}")
        except requests.exceptions.ConnectionError as e:
            self.logger.info(f"[DEBUG] Unexpected ConnectionError happened: {e}")
            return
        except TypeError as e:
            #self.logger.exception(e)
            action_result["status"] = "FAILURE"
            action_result["result"] = f"POST error: {e}"
            self.logger.info(f"[DEBUG] Before typeerror stream result: {e}")
            ret = requests.post("%s%s" % (self.base_url, stream_path), headers=headers, json=action_result)
            #self.logger.info(f"[DEBUG] Result: {ret.status_code}")
            #if ret.status_code != 200:
            #    pr
                
            self.logger.info(f"[DEBUG] TypeError request: Status= {ret.status_code} & Response= {ret.text}")
        except http.client.RemoteDisconnected as e:
            self.logger.info(f"[DEBUG] Expected Remotedisconnect happened: {e}")
            return
        except urllib3.exceptions.ProtocolError as e:
            self.logger.info(f"[DEBUG] Expected ProtocolError happened: {e}")
            return

    async def cartesian_product(self, L):
        if L:
            return {(a, ) + b for a in L[0] for b in await self.cartesian_product(L[1:])}
        else:
            return {()}

    # Handles unique fields by negoiating with the backend 
    def validate_unique_fields(self, params):
        #self.logger.info("IN THE UNIQUE FIELDS PLACE!")

        newlist = [params]
        if isinstance(params, list):
            #self.logger.info("ITS A LIST!")
            newlist = params

        #self.full_execution = os.getenv("FULL_EXECUTION", "") 
        #self.logger.info(len(params))
        #self.logger.info(params.items())
        #self.logger.info(list(params.items()))
        #self.logger.info(f"PARAM: {params}")
        #self.logger.info(f"NEWLIST: {newlist}")

        # FIXME: Also handle MULTI PARAM
        values = []
        param_names = []
        all_values = {}
        index = 0
        for outerparam in newlist:

            #self.logger.info(f"INNERTYPE: {type(outerparam)}")
            #self.logger.info(f"HANDLING PARAM {key}")
            param_value = ""
            for key, value in outerparam.items():
                #self.logger.info("KEY: %s" % key)
                #value = params[key]
                for param in self.action["parameters"]:
                    try:
                        if param["name"] == key and param["unique_toggled"]:
                            self.logger.info(f"[DEBUG] FOUND: {key} with param {param}!")
                            if isinstance(value, dict) or isinstance(value, list):
                                try:
                                    value = json.dumps(value)
                                except json.decoder.JSONDecodeError as e:
                                    self.logger.info(f"[WARNING] Error in json decode for param {value}: {e}")
                                    continue
                            elif isinstance(value, int) or isinstance(value, float):
                                value = str(value)
                            elif value == False:
                                value = "False"
                            elif value == True:
                                value = "True"

                            self.logger.info(f"[DEBUG] VALUE APPEND: {value}")
                            param_value += value
                            if param["name"] not in param_names:
                                param_names.append(param["name"])

                    except (KeyError, NameError) as e:
                        self.logger.info(f"""Key/NameError in param handler for {param["name"]}: {e}""")

            self.logger.info(f"[DEBUG] OUTER VALUE: {param_value}")
            if len(param_value) > 0:
                md5 = hashlib.md5(param_value.encode('utf-8')).hexdigest()
                values.append(md5)
                all_values[md5] = {
                    "index": index, 
                }

            index += 1

            # When in here, it means it should be unique
            # Should this be done by the backend? E.g. ask it if the value is valid?
            # 1. Check if it's unique towards key:value store in org for action
            # 2. Check if COMBINATION is unique towards key:value store of action for org
            # 3. Have a workflow configuration for unique ID's in unison or per field? E.g. if toggled, then send a hash of all fields together alphabetically, but if not, send one field at a time

            # org_id = full_execution["workflow"]["execution_org"]["id"]

            # USE ARRAY?

        new_params = []
        if len(values) > 0:
            org_id = self.full_execution["workflow"]["execution_org"]["id"]
            data = {
                "append": True,
                "workflow_check": False,
                "authorization": self.authorization,
                "execution_ref": self.current_execution_id,
                "org_id": org_id,
                "values": [{
                        "app": self.action["app_name"],
                        "action": self.action["name"],
                        "parameternames": param_names,
                        "parametervalues": values,
                }]
            }

            #self.logger.info(f"DATA: {data}")
            # 1594869a676630b397bc34f7dc0951a3

            #self.logger.info(f"VALUE URL: {url}") 
            #self.logger.info(f"RET: {ret.text}")
            #self.logger.info(f"ID: {ret.status_code}")
            url = f"{self.url}/api/v1/orgs/{org_id}/validate_app_values"
            ret = requests.post(url, json=data)
            if ret.status_code == 200:
                json_value = ret.json()
                if len(json_value["found"]) > 0: 
                    modifier = 0
                    for item in json_value["found"]:
                        self.logger.info(f"Should remove {item}")

                        try:
                            self.logger.info(f"FOUND: {all_values[item]}")
                            self.logger.info(f"SHOULD REMOVE INDEX: {all_values[item]['index']}")

                            try:
                                newlist.pop(all_values[item]["index"]-modifier)
                                modifier += 1
                            except IndexError as e:
                                self.logger.info(f"Error popping value from array: {e}")
                        except (NameError, KeyError) as e:
                            self.logger.info(f"Failed removal: {e}")
                        
                            
                    #return False
                else:
                    self.logger.info("None of the items were found!")
                    return newlist
            else:
                self.logger.info(f"[WARNING] Failed checking values with status code {ret.status_code}!")

        #return True
        return newlist

    # Returns a list of all the executions to be done in the inner loop
    # FIXME: Doesn't take into account whether you actually WANT to loop or not
    # Check if the last part of the value is #?
    async def get_param_multipliers(self, baseparams):
        # Example:
        # {'call': ['hello', 'hello4'], 'call2': ['hello2', 'hello3'], 'call3': '1'}
        # 
        # Should become this because of pairs (all same-length arrays, PROBABLY indicates same source node's values.
        # [
        #   {'call': 'hello', 'call2': 'hello2', 'call3': '1'},
        #   {'call': 'hello4', 'call2': 'hello3', 'call3': '1'}
        # ] 
        # 
        # ----------------------------------------------------------------------
        # Example2:
        # {'call': ['hello'], 'call2': ['hello2', 'hello3'], 'call3': '1'}
        # 
        # Should become this because NOT pairs/triplets:
        # [
        #   {'call': 'hello', 'call2': 'hello2', 'call3': '1'},
        #   {'call': 'hello', 'call2': 'hello3', 'call3': '1'}
        # ] 
        # 
        # ----------------------------------------------------------------------
        # Example3:
        # {'call': ['hello', 'hello2'], 'call2': ['hello3', 'hello4', 'hello5'], 'call3': '1'}
        # 
        # Should become this because arrays are not same length, aka no pairs/triplets. This is the multiplier effect. 2x3 arrays = 6 iterations
        # [
        #   {'call': 'hello', 'call2': 'hello3', 'call3': '1'},
        #   {'call': 'hello', 'call2': 'hello4', 'call3': '1'},
        #   {'call': 'hello', 'call2': 'hello5', 'call3': '1'},
        #   {'call': 'hello2', 'call2': 'hello3', 'call3': '1'},
        #   {'call': 'hello2', 'call2': 'hello4', 'call3': '1'},
        #   {'call': 'hello2', 'call2': 'hello5', 'call3': '1'}
        # ] 
        # To achieve this, we'll do this:
        # 1. For the first array, take the total amount(y) (2x3=6) and divide it by the current array (x): 2. x/y = 3. This means do 3 of each value
        # 2. For the second array, take the total amount(y) (2x3=6) and divide it by the current array (x): 3. x/y = 2. 
        # 3. What does the 3rd array do? Same, but ehhh?
        # 
        # Example4:
        # What if there are multiple loops inside a single item?
        # 
        #

        paramlist = []
        listitems = []
        listlengths = []
        all_lists = []
        all_list_keys = []

        #check_value = "$Filter_list_testing.wrapper.#.tmp"
        #self.action = action

        loopnames = []
        #self.logger.info(f"Baseparams to check!!: {baseparams}")
        for key, value in baseparams.items():
            check_value = ""
            for param in self.original_action["parameters"]:
                if param["name"] == key:
                    #self.logger.info("PARAM: %s" % param)
                    check_value = param["value"]
                    # self.result_wrapper_count = 0

                octothorpe_count = param["value"].count(".#")
                if octothorpe_count > self.result_wrapper_count:
                    self.result_wrapper_count = octothorpe_count
                    self.logger.info("[INFO] NEW OCTOTHORPE WRAPPER: %d" % octothorpe_count)


            # This whole thing is hard.
            # item = [{"data": "1.2.3.4", "dataType": "ip"}] 
            # $item         = DONT loop items. 
            # $item.#       = Loop items
            # $item.#.data  = Loop items
            # With a single item, this is fine.

            # item = [{"list": [{"data": "1.2.3.4", "dataType": "ip"}]}] 
            # $item                 = DONT loop items
            # $item.#               = Loop items
            # $item.#.list          = DONT loop items
            # $item.#.list.#        = Loop items
            # $item.#.list.#.data   = Loop items
            # If the item itself is a list.. hmm
            
            # FIXME: Check the above, and fix so that nested looped items can be 
            # Skipped if wanted

            #self.logger.info("\nCHECK: %s" % check_value)
            #try:
            #    values = parameter["value_replace"]
            #    if values != None:
            #        self.logger.info(values)
            #        for val in values:
            #            self.logger.info(val)
            #except:
            #    pass

            should_merge = False
            if "#" in check_value:
                should_merge = True

            # Specific for OpenAPI body replacement
            #self.logger.info("\n\n\nDOING STUFF BELOW HERE")
            if not should_merge:
                for parameter in self.original_action["parameters"]:
                    if parameter["name"] == key:
                        #self.logger.info("CHECKING BODY FOR VALUE REPLACE DATA!")
                        try:
                            values = parameter["value_replace"]
                            if values != None:
                                self.logger.info(values)
                                for val in values:
                                    if "#" in val["value"]:
                                        should_merge = True
                                        break
                        except:
                            pass

            #self.logger.info(f"MERGE: {should_merge}")
            if isinstance(value, list):
                self.logger.info(f"[DEBUG] Item {value} is a list.")
                if len(value) <= 1:
                    if len(value) == 1:
                        baseparams[key] = value[0]

                    #if "#" in check_value:
                    #    should_merge = True
                else:
                    if not should_merge: 
                        self.logger.info("[DEBUG] Adding WITHOUT looping list")
                    else:
                        if len(value) not in listlengths:
                            listlengths.append(len(value))

                        listitems.append(
                            {
                                key: len(value)
                            }
                        )
                    
                all_list_keys.append(key)
                all_lists.append(baseparams[key])
            else:
                #self.logger.info(f"{value} is not a list")
                pass

        self.logger.info("[DEBUG] Listlengths: %s" % listlengths)
        if len(listlengths) == 0:
            self.logger.info("[DEBUG] NO multiplier. Running a single iteration.")
            paramlist.append(baseparams)
        elif len(listlengths) == 1:
            self.logger.info("[DEBUG] NO MULTIPLIER NECESSARY. Length is %d" % len(listitems))

            for item in listitems:
                # This loops should always be length 1
                for key, value in item.items():
                    if isinstance(value, int):
                        self.logger.info("\n[DEBUG] Should run key %s %d times from %s" % (key, value, baseparams[key]))
                        if len(paramlist) == value:
                            self.logger.info("[DEBUG] List ALREADY exists - just changing values")
                            for subloop in range(value):
                                baseitem = copy.deepcopy(baseparams)
                                paramlist[subloop][key] = baseparams[key][subloop]
                        else:
                            self.logger.info("[DEBUG] List DOESNT exist - ADDING values")
                            for subloop in range(value):
                                baseitem = copy.deepcopy(baseparams)
                                baseitem[key] = baseparams[key][subloop]
                                paramlist.append(baseitem)
                
        else:
            self.logger.info("[DEBUG] Multipliers to handle: %s" % listitems)
            newlength = 1
            for item in listitems:
                for key, value in item.items():
                    newlength = newlength * value

            self.logger.info("[DEBUG] Newlength of array: %d. Lists: %s" % (newlength, all_lists))
            # Get the cartesian product of the arrays
            cartesian = await self.cartesian_product(all_lists)
            newlist = []
            for item in cartesian:
                newlist.append(list(item))

            newobject = {}
            for subitem in range(len(newlist)):
                baseitem = copy.deepcopy(baseparams)
                for key in range(len(newlist[subitem])):
                    baseitem[all_list_keys[key]] = newlist[subitem][key]

                paramlist.append(baseitem)

            #self.logger.info("PARAMLIST: %s" % paramlist)

            #newlist[subitem[0]]
            #if len(newlist) > 0:
            #    itemlength = len(newlist[0])

            # How do we get it back, ordered?
            #for item in cartesian:
            #self.logger.info("Listlengths: %s" % listlengths)
            #paramlist = [baseparams]

        #self.logger.info("[INFO] Return paramlist: %s" % paramlist)
        return paramlist
            

    # Runs recursed versions with inner loops and such 
    async def run_recursed_items(self, func, baseparams, loop_wrapper):
        #self.logger.info(f"RECURSED ITEMS: {baseparams}")
        has_loop = False

        newparams = {}
        for key, value in baseparams.items():
            if isinstance(value, list) and len(value) > 0:
                self.logger.info(f"[DEBUG] In list check for {key}")

                try:
                    # Added skip for body (OpenAPI) which uses data= in requests
                    # Can be screwed up if they name theirs body too 
                    if key != "body":
                        value[0] = json.loads(value[0])
                except json.decoder.JSONDecodeError as e:
                    self.logger.info("[WARNING] JSON casting error (1): %s" % e)
                except TypeError as e:
                    self.logger.info("[WARNING] TypeError: %s" % e)

                self.logger.info("[DEBUG] POST initial list check")

            try:
                if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
                    try:
                        loop_wrapper[key] += 1
                    except Exception as e:
                        self.logger.info("[WARNING] Exception in loop wrapper: {e}")
                        loop_wrapper[key] = 1

                    self.logger.info(f"[DEBUG] Key {key} is a list: {value}")
                    newparams[key] = value[0]
                    has_loop = True 
                else:
                    #self.logger.info(f"Key {key} is NOT a list within a list. Value: {value}")
                    newparams[key] = value
            except Exception as e:
                self.logger.info(f"[WARNING] Error in baseparams list: {e}")
                newparams[key] = value
        
        results = []
        if has_loop:
            self.logger.info(f"[DEBUG] Should run inner loop: {newparams}")
            ret = await self.run_recursed_items(func, newparams, loop_wrapper)
        else:
            self.logger.info(f"[DEBUG] Should run multiplier check with params (inner): {newparams}")
            # 1. Find the loops that are required and create new multipliers
            # If here: check for multipliers within this scope.
            ret = []
            param_multiplier = await self.get_param_multipliers(newparams)

            # FIXME: This does a deduplication of the data
            new_params = self.validate_unique_fields(param_multiplier)
            #self.logger.info(f"NEW PARAMS: {new_params}")
            if len(new_params) == 0:
                self.logger.info("[WARNING] SHOULD STOP MULTI-EXECUTION BECAUSE FIELDS AREN'T UNIQUE")
                self.action_result = {
                    "action": self.action,
                    "authorization": self.authorization,
                    "execution_id": self.current_execution_id,
                    "result": f"All {len(param_multiplier)} values were non-unique",
                    "started_at": self.start_time,
                    "status": "SKIPPED",
                    "completed_at": int(time.time()),
                }

                self.send_result(self.action_result, {"Content-Type": "application/json", "Authorization": "Bearer %s" % self.authorization}, "/api/v1/streams")
                exit()
                #return
            else:
                #subparams = new_params
                #self.logger.info(f"NEW PARAMS: {new_params}")
                param_multiplier = new_params

            #self.logger.info("Returned with newparams of length %d", len(new_params))
            #if isinstance(new_params, list) and len(new_params) == 1:
            #    params = new_params[0]
            #else:
            #    self.logger.info("[WARNING] SHOULD STOP EXECUTION BECAUSE FIELDS AREN'T UNIQUE")
            #    action_result["status"] = "SKIPPED"
            #    action_result["result"] = f"A non-unique value was found"  
            #    action_result["completed_at"] = int(time.time())
            #    self.send_result(action_result, headers, stream_path)
            #    return

            self.logger.info("[INFO] Multiplier length: %d" % len(param_multiplier))
            #tmp = ""
            for subparams in param_multiplier:
                #self.logger.info(f"SUBPARAMS IN MULTI: {subparams}")
                try:
                    #tmp = await func(**subparams)

                    while True:
                        try:
                            tmp = await func(**subparams)
                            break
                        except TypeError as e:
                            self.logger.info("BASE TYPEERROR: %s" % e)
                            errorstring = "%s" % e
                            if "got an unexpected keyword argument" in errorstring:
                                fieldsplit = errorstring.split("'")
                                if len(fieldsplit) > 1:
                                    field = fieldsplit[1]
                    
                                    try:
                                        del subparams[field]
                                        self.logger.info("Removed field invalid field %s" % field)
                                    except KeyError:
                                        break
                            else:
                                raise e

                except:
                    e = ""
                    try:
                        e = sys.exc_info()[1]
                    except:
                        self.logger.info("Exc check fail: %s" % e)
                        pass

                    tmp = "An error occured during execution: %s" % e 

                #self.logger.info("RET from execution: %s" % ret)
                new_value = tmp
                if tmp == None:
                    new_value = ""
                elif isinstance(tmp, dict):
                    new_value = json.dumps(tmp)
                elif isinstance(tmp, list):
                    new_value = json.dumps(tmp)
                #else:
                #tmp = tmp.replace("\"", "\\\"", -1)

                try:
                    new_value = json.loads(new_value)
                except json.decoder.JSONDecodeError as e:
                    pass
                except TypeError as e:
                    pass
                except:
                    pass
                        #self.logger.info("Json: %s" % e)
                        #ret.append(tmp)
                
                #if self.result_wrapper_count > 0:
                #    ret.append("["*(self.result_wrapper_count-1)+new_value+"]"*(self.result_wrapper_count-1))
                #else:
                ret.append(new_value)

            self.logger.info("[INFO] Ret length: %d" % len(ret))
            if len(ret) == 1:
                #ret = ret[0]
                self.logger.info("[DEBUG] DONT make list of 1 into 0!!")

        self.logger.info("Return from execution: %s" % ret)
        if ret == None:
            results.append("")
            json_object = False
        elif isinstance(ret, dict):
            results.append(ret)
            json_object = True
        elif isinstance(ret, list):
            results = ret
            json_object = True
        else:
            ret = ret.replace("\"", "\\\"", -1)

            try:
                results.append(json.loads(ret))
                json_object = True
            except json.decoder.JSONDecodeError as e:
                #self.logger.info("Json: %s" % e)
                results.append(ret)
            except TypeError as e:
                results.append(ret)
            except:
                results.append(ret)

        if len(results) == 1: 
            #results = results[0]
            self.logger.info("DONT MAKE LIST FROM 1 TO 0!!")

        self.logger.info("\nLOOP: %s\nRESULTS: %s" % (loop_wrapper, results))
        return results

    # Downloads all files from a namespace
    # Currently only working on local version of Shuffle
    def get_file_namespace(self, namespace):
        org_id = self.full_execution["workflow"]["execution_org"]["id"]

        get_path = "/api/v1/files/namespaces/%s?execution_id=%s" % (namespace, self.full_execution["execution_id"])
        headers = {
            "Authorization": "Bearer %s" % self.authorization
        }

        ret1 = requests.get("%s%s" % (self.url, get_path), headers=headers)
        if ret1.status_code != 200:
            return None 

        filebytes = BytesIO(ret1.content)
        myzipfile = zipfile.ZipFile(filebytes)
        return myzipfile

    # Things to consider for files:
    # - How can you download / stream a file? 
    # - Can you decide if you want a stream or the files directly?
    def get_file(self, value):
        full_execution = self.full_execution
        org_id = full_execution["workflow"]["execution_org"]["id"]

        self.logger.info("SHOULD GET FILES BASED ON ORG %s, workflow %s and value(s) %s" % (org_id, full_execution["workflow"]["id"], value))

        if isinstance(value, list):
            self.logger.info("IS LIST!")
            #if len(value) == 1:
            #    value = value[0]
        else:
            value = [value]

        returns = []
        for item in value:
            self.logger.info("VALUE: %s" % item)
            if len(item) != 36:
                self.logger.info("Bad length for file value %s" % item)
                continue
                #return {
                #    "filename": "",
                #    "data": "",
                #    "success": False,
                #}

            get_path = "/api/v1/files/%s?execution_id=%s" % (item, full_execution["execution_id"])
            headers = {
                "Content-Type": "application/json",     
                "Authorization": "Bearer %s" % self.authorization
            }

            ret1 = requests.get("%s%s" % (self.url, get_path), headers=headers)
            self.logger.info("RET1 (file get): %s" % ret1.text)
            if ret1.status_code != 200:
                returns.append({
                    "filename": "",
                    "data": "",
                    "success": False,
                })
                continue

            content_path = "/api/v1/files/%s/content?execution_id=%s" % (item, full_execution["execution_id"])
            ret2 = requests.get("%s%s" % (self.url, content_path), headers=headers)
            self.logger.info("RET2 (file get) done")
            if ret2.status_code == 200:
                tmpdata = ret1.json()
                returndata = {
                    "success": True,
                    "filename": tmpdata["filename"],
                    "data": ret2.content,
                }
                returns.append(returndata)

            self.logger.info("RET3 (file get done)")

        if len(returns) == 0:
            return {
                "success": False,
                "filename": "",
                "data": b"",
            }
        elif len(returns) == 1:
            return returns[0]
        else:
            return returns

    def set_cache(self, key, value):
        org_id = self.full_execution["workflow"]["execution_org"]["id"]
        url = "%s/api/v1/orgs/%s/set_cache" % (self.url, org_id)
        data = {
            "workflow_id": self.full_execution["workflow"]["id"],
            "execution_id": self.current_execution_id,
            "authorization": self.authorization,
            "org_id": org_id,
            "key": key,
            "value": str(value),
        }

        response = requests.post(url, json=data)
        try:
            allvalues = response.json()
            allvalues["key"] = key
            allvalues["value"] = str(value)
            return allvalues
        except:
            self.logger.info("Value couldn't be parsed")
            #return response.json()
            return {"success": False}

    def get_cache(self, key):
        org_id = self.full_execution["workflow"]["execution_org"]["id"]
        url = "%s/api/v1/orgs/%s/get_cache" % (self.url, org_id)
        data = {
            "workflow_id": self.full_execution["workflow"]["id"],
            "execution_id": self.current_execution_id,
            "authorization": self.authorization,
            "org_id": org_id,
            "key": key,
        }

        value = requests.post(url, json=data)
        try:
            allvalues = value.json()
            self.logger.info("VAL1: ", allvalues)
            allvalues["key"] = key 
            self.logger.info("VAL2: ", allvalues)

            try:
                parsedvalue = json.loads(allvalues["value"])
                allvalues["value"] = parsedvalue
            except:
                self.logger.info("Parsing of value as JSON failed. Continue anyway!")

            return allvalues
        except:
            self.logger.info("Value couldn't be parsed, or json dump of value failed")
            #return value.json()
            return {"success": False}

    def set_file(self, infiles):
        return self.set_files(infiles)

    # Sets files in the backend
    def set_files(self, infiles):
        full_execution = self.full_execution
        workflow_id = full_execution["workflow"]["id"]
        org_id = full_execution["workflow"]["execution_org"]["id"]
        headers = {
            "Content-Type": "application/json",     
            "Authorization": "Bearer %s" % self.authorization
        }

        if not isinstance(infiles, list):
            infiles = [infiles]

        create_path = "/api/v1/files/create?execution_id=%s" % full_execution["execution_id"]
        file_ids = []
        for curfile in infiles:
            filename = "unspecified"
            data = {
                "filename": filename,
                "workflow_id": workflow_id,
                "org_id": org_id,
            }

            try:
                data["filename"] = curfile["filename"]
                filename = curfile["filename"]
            except KeyError as e:
                self.logger.info(f"KeyError in file setup: {e}")
                pass

            ret = requests.post("%s%s" % (self.url, create_path), headers=headers, json=data)
            self.logger.info(f"Ret CREATE: {ret.text}")
            cur_id = ""
            if ret.status_code == 200:
                self.logger.info("RET: %s" % ret.text)
                ret_json = ret.json()
                if not ret_json["success"]:
                    self.logger.info("Not success in file upload creation.")
                    continue

                self.logger.info("Should handle ID %s" % ret_json["id"])
                file_ids.append(ret_json["id"])
                cur_id = ret_json["id"]
            else:
                self.logger.info("Bad status code: %d" % ret.status_code)
                continue

            if len(cur_id) == 0:
                self.logger.info("No file ID specified from backend")
                continue

            new_headers = {
                "Authorization": f"Bearer {self.authorization}",
            }

            upload_path = "/api/v1/files/%s/upload?execution_id=%s" % (cur_id, full_execution["execution_id"])
            self.logger.info("Create path: %s" % create_path)

            files={"shuffle_file": (filename, curfile["data"])}
            #open(filename,'rb')}

            ret = requests.post("%s%s" % (self.url, upload_path), files=files, headers=new_headers)
            self.logger.info("Ret UPLOAD: %s" % ret.text)
            self.logger.info("Ret2 UPLOAD: %d" % ret.status_code)

        self.logger.info("IDS TO RETURN: %s" % file_ids)
        return file_ids
    
    async def execute_action(self, action):

        # !!! Let this line stay - its used for some horrible codegeneration / stitching !!! # 
        #STARTCOPY
        stream_path = "/api/v1/streams"
        self.action_result = {
            "action": action,
            "authorization": self.authorization,
            "execution_id": self.current_execution_id,
            "result": "",
            "started_at": int(time.time()),
            "status": "EXECUTING"
        }

        # Simple validation of parameters in general
        try:
            tmp_parameters = action["parameters"]
        except KeyError:
            action["parameters"] = []
        except TypeError:
            pass

        self.action = copy.deepcopy(action)
        self.logger.info("[DEBUG] Sending starting action result (EXECUTING)")

        headers = {
            "Content-Type": "application/json",     
            "Authorization": "Bearer %s" % self.authorization
        }

        if len(self.action) == 0:
            self.logger.info("ACTION env not defined")
            self.action_result["result"] = "Error in setup ENV: ACTION not defined"
            self.send_result(self.action_result, headers, stream_path) 
            return

        if len(self.authorization) == 0:
            self.logger.info("AUTHORIZATION env not defined")
            self.action_result["result"] = "Error in setup ENV: AUTHORIZATION not defined"
            self.send_result(self.action_result, headers, stream_path) 
            return

        if len(self.current_execution_id) == 0:
            self.logger.info("EXECUTIONID env not defined")
            self.action_result["result"] = "Error in setup ENV: EXECUTIONID not defined"
            self.send_result(self.action_result, headers, stream_path) 
            return


        # Add async logger
        # self.console_logger.handlers[0].stream.set_execution_id()
        #self.logger.info("Before initial stream result")

        # FIXME: Shouldn't skip this, but it's good for minimzing API calls
        #try:
        #    ret = requests.post("%s%s" % (self.base_url, stream_path), headers=headers, json=action_result)
        #    self.logger.info("Workflow: %d" % ret.status_code)
        #    if ret.status_code != 200:
        #        self.logger.info(ret.text)
        #except requests.exceptions.ConnectionError as e:
        #    self.logger.info("Connectionerror: %s" %  e)

        #    action_result["result"] = "Bad setup during startup: %s" % e 
        #    self.send_result(action_result, headers, stream_path) 
        #    return

        # Verify whether there are any parameters with ACTION_RESULT required
        # If found, we get the full results list from backend
        fullexecution = {}
        if len(self.full_execution) == 0:
            self.logger.info("[DEBUG] NO EXECUTION - LOADING!")
            try:
                tmpdata = {
                    "authorization": self.authorization,
                    "execution_id": self.current_execution_id
                }

                self.logger.info("[DEBUG] Before FULLEXEC stream result")
                ret = requests.post(
                    "%s/api/v1/streams/results" % (self.base_url), 
                    headers=headers, 
                    json=tmpdata
                )

                if ret.status_code == 200:
                    fullexecution = ret.json()
                else:
                    try:
                        self.logger.info("Error: Data: ", ret.json())
                        self.logger.info("Error with status code for results. Crashing because ACTION_RESULTS or WORKFLOW_VARIABLE can't be handled. Status: %d" % ret.status_code)
                    except json.decoder.JSONDecodeError:
                        pass

                    self.action_result["result"] = "Bad result from backend: %d" % ret.status_code
                    self.send_result(self.action_result, headers, stream_path) 
                    return
            except requests.exceptions.ConnectionError as e:
                self.logger.info("Connectionerror: %s" %  e)
                self.action_result["result"] = "Connection error during startup: %s" % e
                self.send_result(self.action_result, headers, stream_path) 
                return
        else:
            try:
                fullexecution = json.loads(self.full_execution)
            except json.decoder.JSONDecodeError as e:
                self.logger.info("[WARNING] Json decode execution error: %s" % e)  
                self.action_result["result"] = "Json error during startup: %s" % e
                self.send_result(self.action_result, headers, stream_path) 
                return

            self.logger.info("")


        self.full_execution = fullexecution
        self.logger.info("[DEBUG] AFTER FULLEXEC stream result (init)")

        # Gets the value at the parenthesis level you want
        def parse_nested_param(string, level):
            """
            Generate strings contained in nested (), indexing i = level
            """
            if len(re.findall("\(", string)) == len(re.findall("\)", string)):
                LeftRightIndex = [x for x in zip(
                [Left.start()+1 for Left in re.finditer('\(', string)], 
                reversed([Right.start() for Right in re.finditer('\)', string)]))]
        
            elif len(re.findall("\(", string)) > len(re.findall("\)", string)):
                return parse_nested_param(string + ')', level)
            elif len(re.findall("\(", string)) < len(re.findall("\)", string)):
                return parse_nested_param('(' + string, level)
            else:
                return 'Failed to parse params'
        
            try:
                return [string[LeftRightIndex[level][0]:LeftRightIndex[level][1]]]
            except IndexError:
                return [string[LeftRightIndex[level+1][0]:LeftRightIndex[level+1][1]]]
        
        # Finds the deepest level parenthesis in a string
        def maxDepth(S): 
            current_max = 0
            max = 0
            n = len(S) 
          
            # Traverse the input string 
            for i in range(n): 
                if S[i] == '(': 
                    current_max += 1
          
                    if current_max > max: 
                        max = current_max 
                elif S[i] == ')': 
                    if current_max > 0: 
                        current_max -= 1
                    else: 
                        return -1
          
            # finally check for unbalanced string 
            if current_max != 0: 
                return -1
          
            return max-1
        
        # Specific type parsing
        def parse_type(data, thistype): 
            if data == None:
                return "Empty"
        
            if "int" in thistype or "number" in thistype:
                try:
                    return int(data)
                except ValueError:
                    return data

            if "lower" in thistype:
                return data.lower()
            if "upper" in thistype:
                return data.upper()
            if "trim" in thistype:
                return data.strip()
            if "strip" in thistype:
                return data.strip()
            if "split" in thistype:
                return data.split()
            if "replace" in thistype:
                splitvalues = data.split(",")

                if len(splitvalues) > 2:
                    for i in range(len(splitvalues)):
                        if i != 0:
                            if splitvalues[i] == "  ":
                                splitvalues[i] = " "
                                continue

                            splitvalues[i] = splitvalues[i].strip()

                            if splitvalues[i] == "\"\"":
                                splitvalues[i] = ""
                            if splitvalues[i] == "\" \"":
                                splitvalues[i] = " "
                            if len(splitvalues[i]) > 2:
                                if splitvalues[i][0] == "\"" and splitvalues[i][len(splitvalues[i])-1] == "\"":
                                    splitvalues[i] = splitvalues[i][1:-1]
                                if splitvalues[i][0] == "'" and splitvalues[i][len(splitvalues[i])-1] == "'":
                                    splitvalues[i] = splitvalues[i][1:-1]
                            

                    replacementvalue = splitvalues[0]
                    return replacementvalue.replace(splitvalues[1], splitvalues[2], -1) 
                else: 
                    return f"replace({data})"
            if "join" in thistype:
                try:
                    splitvalues = data.split(",")
                    if "," not in data:
                        return f"join({data})"

                    if len(splitvalues) >= 2:

                        # 1. Take the list and parse it from string
                        # 2. Take all the items and join them
                        # 3. Parse them back as string and return
                        values = ",".join(splitvalues[0:-1])
                        tmp = json.loads(values)
                        try:
                            newvalues = splitvalues[-1].join(str(item).strip() for item in tmp)
                        except TypeError:
                            newvalues = splitvalues[-1].join(json.dumps(item).strip() for item in tmp)

                        return newvalues
                    else:
                        return f"join({data})"

                except (KeyError, IndexError) as e:
                    print(f"ERROR in join(): {e}")
                except json.decoder.JSONDecodeError as e:
                    print(f"JSON ERROR in join(): {e}")

            if "len" in thistype or "length" in thistype or "lenght" in thistype:
                #self.logger.info(f"Trying to length-parse: {data}")
                try:
                    tmp_len = json.loads(data, parse_float=str, parse_int=str, parse_constant=str)
                except (NameError, KeyError, TypeError, json.decoder.JSONDecodeError) as e:
                    try:
                        #self.logger.info(f"[WARNING] INITIAL Parsing bug for length in app sdk: {e}")
                        # data = data.replace("\'", "\"")
                        data = data.replace("True", "true")
                        data = data.replace("False", "false")
                        data = data.replace("None", "null")
                        data = data.replace("\"", "\\\"")
                        data = data.replace("'", "\"")

                        tmp_len = json.loads(data, parse_float=str, parse_int=str, parse_constant=str)
                    except (NameError, KeyError, TypeError, json.decoder.JSONDecodeError) as e:
                        tmp_len = str(data)

                return str(len(tmp_len))

            if "parse" in thistype:
                splitvalues = []
                default_error = """Error. Expected syntax: parse(["hello","test1"],0:1)""" 
                if "," in data:
                    splitvalues = data.split(",")

                    for item in range(len(splitvalues)):
                        splitvalues[item] = splitvalues[item].strip()
                else:
                    return default_error 

                lastsplit = []
                if ":" in splitvalues[-1]:
                    lastsplit = splitvalues[-1].split(":")
                else:
                    try:
                        lastsplit = [int(splitvalues[-1])]
                    except ValueError:
                        return default_error

                try:
                    parsedlist = ",".join(splitvalues[0:-1])
                    if len(lastsplit) > 1:
                        tmp = json.loads(parsedlist)[int(lastsplit[0]):int(lastsplit[1])]
                    else:
                        tmp = json.loads(parsedlist)[lastsplit[0]]

                    return tmp
                except IndexError as e:
                    return default_error

        # Parses the INNER value and recurses until everything is done
        # Looks for a way to use e.g. int() or number() as a value
        def parse_wrapper(data):
            try:
                if "(" not in data or ")" not in data:
                    return data, False
            except TypeError:
                return data, False

            wrappers = ["int", "number", "lower", "upper", "trim", "strip", "split", "parse", "len", "length", "lenght", "join", "replace"]

            if not any(wrapper in data for wrapper in wrappers):
                return data, False

            # Do stuff here.
            inner_value = parse_nested_param(data, maxDepth(data) - 0)
            outer_value = parse_nested_param(data, maxDepth(data) - 1)

            wrapper_group = "|".join(wrappers)
            parse_string = data
            max_depth = maxDepth(parse_string)

            if outer_value != inner_value:
                for casting_items in reversed(range(max_depth + 1)):
                    c_parentheses = parse_nested_param(parse_string, casting_items)[0]
                    match_string = re.escape(c_parentheses)
                    custom_casting = re.findall(fr"({wrapper_group})\({match_string}", parse_string)

                    # no matching ; go next group
                    if len(custom_casting) == 0:
                        continue

                    inner_result = parse_type(c_parentheses, custom_casting[0])

                    # if result is a string then parse else return
                    if isinstance(inner_result, str):
                        parse_string = parse_string.replace(f"{custom_casting[0]}({c_parentheses})", inner_result)
                    elif isinstance(inner_result, list):
                        parse_string = parse_string.replace(f"{custom_casting[0]}({c_parentheses})",
                                                            json.dumps(inner_result))
                    else:
                        parse_string = inner_result
                        break
            else:
                c_parentheses = parse_nested_param(parse_string, 0)[0]
                match_string = re.escape(c_parentheses)
                custom_casting = re.findall(fr"({wrapper_group})\({match_string}", parse_string)
                print("[DEBUG] In ELSE: %s" % custom_casting)
                # check if a wrapper was found
                if len(custom_casting) != 0:
                    inner_result = parse_type(c_parentheses, custom_casting[0])
                    if isinstance(inner_result, str):
                        parse_string = parse_string.replace(f"{custom_casting[0]}({c_parentheses})", inner_result)
                    elif isinstance(inner_result, list):
                        parse_string = parse_string.replace(f"{custom_casting[0]}({c_parentheses})",
                                                            json.dumps(inner_result))
                    else:
                        parse_string = inner_result

            print("PARSE STRING: %s" % parse_string)
            return parse_string, True

        # Looks for parantheses to grab special cases within a string, e.g:
        # int(1) lower(HELLO) or length(what's the length)
        # FIXME: 
        # There is an issue in here where it returns data wrong. Example:
        # Authorization=Bearer authkey
        # =
        # Authorization=Bearer  authkey
        # ^ Double space.
        def parse_wrapper_start(data, self):
            try:
                data = parse_liquid(data, self)
            except:
                pass

            if "(" not in data or ")" not in data:
                return data

            if isinstance(data, str) and len(data) > 4:
                if (data[0] == "{" or data[0] == "[") and (data[len(data)-1] == "]" or data[len(data)-1] == "}"):
                    self.logger.info("Skipping parser because use of {[ and ]}")
                    return data

            newdata = []
            newstring = ""
            record = True
            paranCnt = 0
            charcnt = 0 
            for char in data:
                if char == "(":
                    charskip = False
                    if charcnt > 0:
                        if data[charcnt-1] == " ":
                            charskip = True 

                    if not charskip:
                        paranCnt += 1
            
                        if not record:
                            record = True 
        
                if record:
                    newstring += char
        
                if paranCnt == 0 and char == " ":
                    newdata.append(newstring)
                    newstring = ""
                    record = True
        
                if char == ")":
                    paranCnt -= 1
        
                    if paranCnt == 0:
                        record = False
            
                charcnt += 1
        
            if len(newstring) > 0:
                newdata.append(newstring)
        
            parsedlist = []
            non_string = False
            parsed = False
            for item in newdata:
                ret = parse_wrapper(item)
                if not isinstance(ret[0], str):
                    non_string = True
            
                parsedlist.append(ret[0])
                if ret[1]:
                    parsed = True
            
            if not parsed:
                return data
        
            if len(parsedlist) > 0 and not non_string:
                #self.logger.info("Returning parsed list: ", parsedlist)
                return " ".join(parsedlist)
            elif len(parsedlist) == 1 and non_string:
                return parsedlist[0]
            else:
                #self.logger.info("Casting back to string because multi: ", parsedlist)
                newlist = []
                for item in parsedlist:
                    try:
                        newlist.append(str(item))
                    except ValueError:
                        newlist.append("parsing_error")

                # Does this create the issue?
                return " ".join(newlist)

        # Parses JSON loops and such down to the item you're looking for
        # $nodename.#.id 
        # $nodename.data.#min-max.info.id
        def recurse_json(basejson, parsersplit):
            match = "#([0-9a-z]+):?-?([0-9a-z]+)?#?"
            try:
                outercnt = 0

                # Loops over split values
                for value in parsersplit:
                    #if " " in value:
                    #    value = value.replace(" ", "_", -1)

                    actualitem = re.findall(match, value, re.MULTILINE)
                    if value == "#":
                        newvalue = []
                        for innervalue in basejson:
                            # 1. Check the next item (message)
                            # 2. Call this function again
        
                            try:
                                ret, is_loop = recurse_json(innervalue, parsersplit[outercnt+1:])
                            except IndexError:
                                # Only in here if it's the last loop without anything in it?
                                ret, is_loop = recurse_json(innervalue, parsersplit[outercnt:])
                                
                            newvalue.append(ret)
                        
                        # Magical way of returning which makes app sdk identify 
                        # it as multi execution
                        return newvalue, True

                    elif len(actualitem) > 0:

                        is_loop = True
                        newvalue = []
                        firstitem = actualitem[0][0]
                        seconditem = actualitem[0][1]
                        print("[DEBUG] ACTUAL PARSED: %s" % actualitem)

                        # Means it's a single item -> continue
                        if seconditem == "":
                            print("[INFO] In first - handling %s. Len: %d" % (firstitem, len(basejson)))
                            if firstitem.lower() == "max" or firstitem.lower() == "last": 
                                firstitem = len(basejson)-1
                            elif firstitem.lower() == "min" or firstitem.lower() == "first": 
                                firstitem = 0
                            else:
                                firstitem = int(firstitem)

                            print(f"[DEBUG] Post lower checks with item {firstitem}")
                            tmpitem = basejson[int(firstitem)]
                            try:
                                newvalue, is_loop = recurse_json(tmpitem, parsersplit[outercnt+1:])
                            except IndexError:
                                newvalue, is_loop = (tmpitem, parsersplit[outercnt+1:])
                        else:
                            print("[INFO] In ELSE - handling %s and %s" % (firstitem, seconditem))
                            if firstitem.lower() == "max" or firstitem.lower() == "last": 
                                firstitem = len(basejson)-1
                            elif firstitem.lower() == "min" or firstitem.lower() == "first": 
                                firstitem = 0
                            else:
                                firstitem = int(firstitem)

                            if seconditem.lower() == "max" or seconditem.lower() == "last": 
                                seconditem = len(basejson)-1
                            elif seconditem.lower() == "min" or seconditem.lower() == "first": 
                                seconditem = 0
                            else:
                                seconditem = int(seconditem)

                            print(f"[DEBUG] Post lower checks 2: {firstitem} AND {seconditem}")
                            newvalue = []
                            if int(seconditem) > len(basejson):
                                seconditem = len(basejson)

                            for i in range(int(firstitem), int(seconditem)+1):
                                # 1. Check the next item (message)
                                # 2. Call this function again
                                #self.logger.info("Base: %s" % basejson[i])

                                try:
                                    ret, tmp_loop = recurse_json(basejson[i], parsersplit[outercnt+1:])
                                except IndexError:
                                    print("[DEBUG] INDEXERROR: ", parsersplit[outercnt])
                                    #ret = innervalue
                                    ret, tmp_loop = recurse_json(basejson[i], parsersplit[outercnt:])
                                    
                                newvalue.append(ret)

                        return newvalue, is_loop 

                    else:
                        if len(value) == 0:
                            return basejson, False
        
                        try:
                            if isinstance(basejson, list): 
                                print("[WARNING] VALUE IN ISINSTANCE IS NOT TO BE USED (list): %s" % value)
                                return basejson, False
                            elif isinstance(basejson[value], str):
                                try:
                                    basejson = json.loads(basejson[value])
                                except json.decoder.JSONDecodeError as e:
                                    return basejson[value], False
                            else:
                                basejson = basejson[value]
                        except KeyError as e:
                            print("[WARNING] Running secondary value check with replacement of underscore in %s: %s" % (value, e))
                            if "_" in value:
                                value = value.replace("_", " ", -1)
                            elif " " in value:
                                value = value.replace(" ", "_", -1)

                            if isinstance(basejson, list): 
                                print("[WARNING] VALUE IN ISINSTANCE IS NOT TO BE USED (list): %s" % value)
                                return basejson, False
                            elif isinstance(basejson[value], str):
                                print(f"[INFO] LOADING STRING '%s' AS JSON" % basejson[value]) 
                                try:
                                    basejson = json.loads(basejson[value])
                                    print("[DEBUG] BASEJSON: %s" % basejson)
                                except json.decoder.JSONDecodeError as e:
                                    print("[DEBUG] RETURNING BECAUSE '%s' IS A NORMAL STRING (1)" % basejson[value])
                                    return basejson[value], False
                            else:
                                basejson = basejson[value]
                            

                    outercnt += 1
        
            except KeyError as e:
                print("[INFO] Lower keyerror: %s" % e)
                return "", False

                #return basejson
                #return "KeyError: Couldn't find key: %s" % e

            return basejson, False

        # Takes a workflow execution as argument
        # Returns a string if the result is single, or a list if it's a list
        def get_json_value(execution_data, input_data):
            parsersplit = input_data.split(".")
            actionname_lower = parsersplit[0][1:].lower()

            #Actionname: Start_node
            print(f"\n[INFO] Actionname: {actionname_lower}")

            # 1. Find the action
            baseresult = ""

            appendresult = "" 
            print("[INFO] Parsersplit length: %d" % len(parsersplit))
            if (actionname_lower.startswith("exec ") or actionname_lower.startswith("webhook ") or actionname_lower.startswith("schedule ") or actionname_lower.startswith("userinput ") or actionname_lower.startswith("email_trigger ") or actionname_lower.startswith("trigger ")) and len(parsersplit) == 1:
                record = False
                for char in actionname_lower:
                    if char == " ":
                        record = True

                    if record:
                        appendresult += char

                actionname_lower = "exec"
            elif actionname_lower.startswith("shuffle_cache "): 
                actionname_lower = "shuffle_cache"

            actionname_lower = actionname_lower.replace(" ", "_", -1)

            try: 
                if actionname_lower == "exec" or actionname_lower == "webhook" or actionname_lower == "schedule" or actionname_lower == "userinput" or actionname_lower == "email_trigger" or actionname_lower == "trigger": 
                    baseresult = execution_data["execution_argument"]
                elif actionname_lower == "shuffle_cache":
                    print("[DEBUG] SHOULD GET CACHE KEY: %s" % parsersplit) 
                    if len(parsersplit) > 1:
                        actual_key = parsersplit[1]
                        print("[DEBUG] KEY: %s" % actual_key)
                        cachedata = self.get_cache(actual_key)
                        print("CACHE: %s" % cachedata)
                        parsersplit.pop(1)
                        try:
                            baseresult = json.dumps(cachedata)
                        except json.decoder.JSONDecodeError as e:
                            print("[WARNING] Failed json dumping: %s" % e)

                else:
                    if execution_data["results"] != None:
                        for result in execution_data["results"]:
                            resultlabel = result["action"]["label"].replace(" ", "_", -1).lower()
                            if resultlabel.lower() == actionname_lower:
                                baseresult = result["result"]
                                break
                    else:
                        print("[DEBUG] No results to get values from.")
                        baseresult = "$" + parsersplit[0][1:] 
                    
                    print("[DEBUG] BEFORE VARIABLES!")
                    if len(baseresult) == 0:
                        try:
                            for variable in execution_data["workflow"]["workflow_variables"]:
                                variablename = variable["name"].replace(" ", "_", -1).lower()
        
                                if variablename.lower() == actionname_lower:
                                    baseresult = variable["value"]
                                    break

                        except KeyError as e:
                            print("[INFO] KeyError wf variables: %s" % e)
                            pass
                        except TypeError as e:
                            print("[INFO] TypeError wf variables: %s" % e)
                            pass
        
                    print("[DEBUG] BEFORE EXECUTION VAR")
                    if len(baseresult) == 0:
                        try:
                            for variable in execution_data["execution_variables"]:
                                variablename = variable["name"].replace(" ", "_", -1).lower()
                                if variablename.lower() == actionname_lower:
                                    baseresult = variable["value"]
                                    break
                        except KeyError as e:
                            print("[INFO] KeyError exec variables: %s" % e)
                            pass
                        except TypeError as e:
                            print("[INFO] TypeError exec variables: %s" % e)
                            pass
        
            except KeyError as error:
                print(f"[DEBUG] KeyError in JSON: {error}")
        
            print(f"[INFO] After first trycatch. Baseresult")#, baseresult)
        
            # 2. Find the JSON data
            if len(baseresult) == 0:
                return ""+appendresult, False
        
            print("[INFO] After second return")
            if len(parsersplit) == 1:
                returndata = str(baseresult)+str(appendresult)
                print("[DEBUG] RETURNING!")#: %s" % returndata)
                return returndata, False
        
            baseresult = baseresult.replace(" True,", " true,")
            baseresult = baseresult.replace(" False", " false,")

            print("[INFO] After third parser return - Formatted")#, baseresult)
            basejson = {}
            try:
                basejson = json.loads(baseresult)
            except json.decoder.JSONDecodeError as e:
                try:
                    baseresult = baseresult.replace("\'", "\"")
                    basejson = json.loads(baseresult)
                except json.decoder.JSONDecodeError as e:
                    print("Parser issue with JSON: %s" % e)
                    return str(baseresult)+str(appendresult), False

            print("[INFO] After fourth parser return as JSON")
            data, is_loop = recurse_json(basejson, parsersplit[1:])
            parseditem = data

            if isinstance(parseditem, dict) or isinstance(parseditem, list):
                try:
                    parseditem = json.dumps(parseditem)
                except json.decoder.JSONDecodeError as e:
                    print("[WARNING] Parseditem issue: %s" % e)
                    pass

            print("[DEBUG] DATA: (%s) %s" % (type(data), data))
            if is_loop:
                print("[DEBUG] DATA IS A LOOP - SHOULD WRAP")
                if parsersplit[-1] == "#":
                    print("[WARNING] SET DATA WRAPPER TO NORMAL!")
                    parseditem = "${SHUFFLE_NO_SPLITTER%s}$" % json.dumps(data)
                else:
                    # Return value: ${id[12345, 45678]}$
                    print("[WARNING] SET DATA WRAPPER TO %s!" % parsersplit[-1])
                    parseditem = "${%s%s}$" % (parsersplit[-1], json.dumps(data))


            print("[DEBUG] Before last return with %s" % appendresult)
            returndata = str(parseditem)+str(appendresult)

            # New in 0.8.97: Don't return items without lists
            #self.logger.info("RETURNDATA: %s" % returndata)
            #return returndata, is_loop
            try:
                return json.dumps(json.loads(returndata)), is_loop
            except json.decoder.JSONDecodeError as e:
                print("Error in decoder: %s" % e)
                return returndata, is_loop

        # Sending self as it's not a normal function
        def parse_liquid(template, self):

            #self.logger.info("Inside liquid with glob: %s" % globals())
            try:
                if len(template) > 5000000:
                    self.logger.info("[DEBUG] Skipping liquid - size too big (%d)" % len(template))
                    return template

                #if not "{{" in template or not "}}" in template: 
                #    if not "{%" in template or not "%}" in template: 
                #        self.logger.info("Skipping liquid - missing {{ }} and {% %}")
                #        return template

                #if not "{{" in template or not "}}" in template: 
                #    return template

                #self.logger.info(globals())
                self.logger.info("[DEBUG] Running liquid with data of length %d" % len(template))
                run = Liquid(template, mode="wild", from_file=False)

                # Can't handle self yet (?)
                ret = run.render(**globals())
                return ret
                #try:
                    #run = Liquid(template)
                    #return ret
                #except liquid.exceptions.LiquidSyntaxError as  e:
                #    run = Liquid(template, {'mode': 'python'})
                #    ret = run.render(**globals())
                #    return ret
                #except liquid.exceptions.LiquidRenderError as e:
                #    self.logger.info("Render error: %s" % e)
            except jinja2.exceptions.TemplateNotFound as e:
                self.logger.info("[ERROR] Template error: %s" % e)
            except jinja2.exceptions.TemplateSyntaxError as e:
                self.logger.info("[ERROR] Syntax error: %s" % e)
            except:
                self.logger.info("[ERROR] General exception for liquid")

            return template

        # Suboptimal cleanup script for BOdy parsing of OpenAPI
        # Should have a regex which looks for the value, then goes out and cleans up the key
        def recurse_cleanup_script(data):
            deletekeys = []
            newvalue = data
            try:
                if not isinstance(data, dict):
                    newvalue = json.loads(data)
                else:
                    newvalue = data
        
                for key, value in newvalue.items():
                    if isinstance(value, str) and len(value) == 0:
                        deletekeys.append(key)
                        continue
                            
                    if isinstance(value, list):
                        try:
                            value = json.dumps(value)
                        except:
                            print("[WARNING] Json parsing issue in recursed value")
                            pass
        
                    if value == "${%s}" % key:
                        print("[WARNING] Deleting %s because key = value" % key)
                        deletekeys.append(key)
                        continue
                    elif "${" in value and "}" in value:
                        print("[WARNING] Deleting %s because it contains ${ and }" % key)
                        deletekeys.append(key)
                        continue
        
                    if isinstance(value, dict):
                        newvalue[key] = recurse_cleanup_script(value)
        
            except json.decoder.JSONDecodeError as e:
                print(f"[WARNING] Failed JSON replacement for OpenAPI keys (3) {e}")
            except Exception as e:
                print(f"[WARNING] Failed as an exception (1): {e}") 
                
            try:
                for deletekey in deletekeys:
                    try:
                        del newvalue[deletekey]
                    except:
                        pass
            except Exception as e:
                print(f"[WARNING] Failed in deletekeys: {e}") 
                return data
        
            try:
                for key, value in newvalue.items():
                    if isinstance(value, bool):
                        continue
                    elif isinstance(value, dict) and not bool(value):
                        continue
        
                    try:
                        value = json.loads(value)
                        newvalue[key] = value
                    except json.decoder.JSONDecodeError as e:
                        continue
                    except Exception as e:
                        continue
        
                try:
                    data = json.dumps(newvalue)
                except json.decoder.JSONDecodeError as e:
                    print("[WARNING] JsonDecodeError: %s" % e)
                    data = newvalue
        
            except json.decoder.JSONDecodeError as e:
                print("[WARNING] Failed JSON replacement for OpenAPI keys (2) {e}")
            except Exception as e:
                print(f"[WARNING] Failed as an exception (2): {e}") 
        
            return data 

        # Parses parameters sent to it and returns whether it did it successfully with the values found
        def parse_params(action, fullexecution, parameter, self):
            # Skip if it starts with $?
            jsonparsevalue = "$."
            is_loop = False

            # Matches with space in the first part, but not in subsequent parts.
            # JSON / yaml etc shouldn't have spaces in their fields anyway.
            #match = ".*?([$]{1}([a-zA-Z0-9 _-]+\.?){1}([a-zA-Z0-9#_-]+\.?){0,})[$/, ]?"
            #match = ".*?([$]{1}([a-zA-Z0-9 _-]+\.?){1}([a-zA-Z0-9#_-]+\.?){0,})"

            match = ".*?([$]{1}([a-zA-Z0-9_-]+\.?){1}([a-zA-Z0-9#_-]+\.?){0,})" # Removed space - no longer ok. Force underscore.

            # Extra replacements for certain scenarios
            escaped_dollar = "\\$"
            escape_replacement = "\\%\\%\\%\\%\\%"
            end_variable = "^_^"

            #self.logger.info("Input value: %s" % parameter["value"])
            try:
                parameter["value"] = parameter["value"].replace(escaped_dollar, escape_replacement, -1)
            except:
                self.logger.info("Error in initial replacement of escaped dollar!")

            #self.logger.info("POST input value: %s" % parameter["value"])

            # Regex to find all the things
            if parameter["variant"] == "STATIC_VALUE":
                data = parameter["value"]
                actualitem = re.findall(match, data, re.MULTILINE)
                #self.logger.debug(f"\n\nHandle static data with JSON: {data}\n\n")
                #self.logger.info("STATIC PARSED: %s" % actualitem)
                if len(actualitem) > 0:
                    #self.logger.info("[DEACTUAL: ", actualitem)
                    for replace in actualitem:
                        try:
                            to_be_replaced = replace[0]
                        except IndexError:
                            continue

                        # Handles for loops etc. 
                        # FIXME: Should it dump to string here? Doesn't that defeat the purpose?
                        # Trying without string dumping.

                        value, is_loop = get_json_value(fullexecution, to_be_replaced) 
                        #self.logger.info("\n\nType of value: %s. Value: %s" % (type(value), value))
                        self.logger.info("\n\nType of value: %s" % type(value))
                        if isinstance(value, str):
                            parameter["value"] = parameter["value"].replace(to_be_replaced, value)
                        elif isinstance(value, dict) or isinstance(value, list):
                            # Changed from JSON dump to str() 28.05.2021
                            # This makes it so the parameters gets lists and dicts straight up
                            parameter["value"] = parameter["value"].replace(to_be_replaced, json.dumps(value))

                            #try:
                            #    parameter["value"] = parameter["value"].replace(to_be_replaced, str(value))
                            #except:
                            #    parameter["value"] = parameter["value"].replace(to_be_replaced, json.dumps(value))
                            #    self.logger.info("Failed parsing value as string?")
                        else:
                            self.logger.info("Unknown type %s" % type(value))
                            try:
                                parameter["value"] = parameter["value"].replace(to_be_replaced, json.dumps(value))
                            except json.decoder.JSONDecodeError as e:
                                parameter["value"] = parameter["value"].replace(to_be_replaced, value)

                        #self.logger.info("VALUE: %s" % parameter["value"])

            if parameter["variant"] == "WORKFLOW_VARIABLE":
                self.logger.info("[DEBUG] Handling workflow variable")
                found = False
                try:
                    for item in fullexecution["workflow"]["workflow_variables"]:
                        if parameter["action_field"] == item["name"]:
                            found = True
                            parameter["value"] = item["value"]
                            break
                except KeyError as e:
                    self.logger.info("KeyError WF variable 1: %s" % e)
                    pass
                except TypeError as e:
                    self.logger.info("TypeError WF variables 1: %s" % e)
                    pass

                if not found:
                    try:
                        for item in fullexecution["execution_variables"]:
                            if parameter["action_field"] == item["name"]:
                                parameter["value"] = item["value"]
                                break
                    except KeyError as e:
                        self.logger.info("KeyError WF variable 2: %s" % e)
                        pass
                    except TypeError as e:
                        self.logger.info("TypeError WF variables 2: %s" % e)
                        pass

            elif parameter["variant"] == "ACTION_RESULT":
                # FIXME - calculate value based on action_field and $if prominent
                # FIND THE RIGHT LABEL
                # GET THE LABEL'S RESULT 
                
                tmpvalue = ""
                self.logger.info("ACTION FIELD: %s" % parameter["action_field"])

                fullname = "$"
                if parameter["action_field"] == "Execution Argument":
                    tmpvalue = fullexecution["execution_argument"]
                    fullname += "exec"
                else:
                    fullname += parameter["action_field"]

                self.logger.info("PRE Fullname: %s" % fullname)

                if parameter["value"].startswith(jsonparsevalue):
                    fullname += parameter["value"][1:]
                #else:
                #    fullname = "$%s" % parameter["action_field"]

                self.logger.info("Fullname: %s" % fullname)
                actualitem = re.findall(match, fullname, re.MULTILINE)
                self.logger.info("ACTION PARSED: %s" % actualitem)
                if len(actualitem) > 0:
                    for replace in actualitem:
                        try:
                            to_be_replaced = replace[0]
                        except IndexError:
                            self.logger.info("Nothing to replace?: " % e)
                            continue
                        
                        # This will never be a loop aka multi argument
                        parameter["value"] = to_be_replaced 

                        value, is_loop = get_json_value(fullexecution, to_be_replaced)
                        self.logger.info("Loop: %s" % is_loop)
                        if isinstance(value, str):
                            parameter["value"] = parameter["value"].replace(to_be_replaced, value)
                        elif isinstance(value, dict):
                            parameter["value"] = parameter["value"].replace(to_be_replaced, json.dumps(value))
                        else:
                            self.logger.info("Unknown type %s" % type(value))
                            try:
                                parameter["value"] = parameter["value"].replace(to_be_replaced, json.dumps(value))
                            except json.decoder.JSONDecodeError as e:
                                parameter["value"] = parameter["value"].replace(to_be_replaced, value)

            #self.logger.info("PRE Replaced data: %s" % parameter["value"])

            try:
                parameter["value"] = parameter["value"].replace(end_variable, "", -1)
                parameter["value"] = parameter["value"].replace(escape_replacement, "$", -1)
            except:
                self.logger.info("Error in datareplacement")

            # Just here in case it breaks 
            # Implemented 02.08.2021
            #self.logger.info("Pre liquid: %s" % parameter["value"])
            try:
                parameter["value"] = parse_liquid(parameter["value"], self)
            except:
                pass

            #self.logger.info("Replaced data: %s" % parameter["value"])

            #self.logger.info("POST liquid: %s" % parameter["value"])
            return "", parameter["value"], is_loop

        def run_validation(sourcevalue, check, destinationvalue):
            print("[DEBUG] Checking %s %s %s" % (sourcevalue, check, destinationvalue))

            if check == "=" or check.lower() == "equals":
                if str(sourcevalue).lower() == str(destinationvalue).lower():
                    return True
            elif check == "!=" or check.lower() == "does not equal":
                if str(sourcevalue).lower() != str(destinationvalue).lower():
                    return True
            elif check.lower() == "startswith":
                if str(sourcevalue).lower().startswith(str(destinationvalue).lower()):
                    return True
            elif check.lower() == "endswith":
                if str(sourcevalue).lower().endswith(str(destinationvalue).lower()):
                    return True
            elif check.lower() == "contains":
                if destinationvalue.lower() in sourcevalue.lower():
                    return True

            elif check.lower() == "is empty":
                if len(sourcevalue) == 0:
                    return True

                if str(sourcevalue) == 0:
                    return True

                return False
                #if tmp == "[]":
                #    tmp = []

                #if type(tmp) == list and len(tmp) == 0 and not flip:
                #    new_list.append(item)
                #elif type(tmp) == list and len(tmp) > 0 and flip:
                #    new_list.append(item)
                #elif type(tmp) == str and not tmp and not flip:
                #    new_list.append(item)
                #elif type(tmp) == str and tmp and flip:
                #    new_list.append(item)
                #else:
                #    failed_list.append(item)

            elif check.lower() == "contains_any_of":
                newvalue = [destinationvalue.lower()]
                if "," in destinationvalue:
                    newvalue = destinationvalue.split(",")
                elif ", " in destinationvalue:
                    newvalue = destinationvalue.split(", ")

                for item in newvalue:
                    if not item:
                        continue

                    if item.strip() in sourcevalue:
                        print("[INFO] Found %s in %s" % (item, sourcevalue))
                        return True
                    
                return False 
            elif check.lower() == "larger than" or check.lower() == "bigger than":
                try:
                    if str(sourcevalue).isdigit() and str(destinationvalue).isdigit():
                        if int(sourcevalue) > int(destinationvalue):
                            return True

                except AttributeError as e:
                    print("[WARNING] Condition larger than failed with values %s and %s: %s" % (sourcevalue, destinationvalue, e))
                    return False
            elif check.lower() == "smaller than" or check.lower() == "less than":
                try:
                    if str(sourcevalue).isdigit() and str(destinationvalue).isdigit():
                        if int(sourcevalue) < int(destinationvalue):
                            return True

                except AttributeError as e:
                    print("[WARNING] Condition smaller than failed with values %s and %s: %s" % (sourcevalue, destinationvalue, e))
                    return False
            elif check.lower() == "re" or check.lower() == "matches regex":
                try:
                    found = re.search(destinationvalue, sourcevalue)
                except re.error as e:
                    print("[WARNING] Regex error in condition: %s" % e)
                    return False

                if found == None:
                    return False

                return True
            else:
                print("[DEBUG] Condition: can't handle %s yet. Setting to true" % check)
                    
            return False

        def check_branch_conditions(action, fullexecution, self):
            # relevantbranches = workflow.branches where destination = action
            try:
                if fullexecution["workflow"]["branches"] == None or len(fullexecution["workflow"]["branches"]) == 0:
                    return True, ""
            except KeyError:
                return True, ""

            relevantbranches = []
            for branch in fullexecution["workflow"]["branches"]:
                if branch["destination_id"] != action["id"]:
                    continue

                # Remove anything without a condition
                try:
                    if (branch["conditions"]) == 0 or branch["conditions"] == None:
                        continue
                except KeyError:
                    continue

                self.logger.info("Relevant conditions: %s" % branch["conditions"])
                successful_conditions = []
                failed_conditions = []
                for condition in branch["conditions"]:
                    self.logger.info("Getting condition value of %s" % condition)

                    # Parse all values first here
                    sourcevalue = condition["source"]["value"]
                    check, sourcevalue, is_loop = parse_params(action, fullexecution, condition["source"], self)
                    if check:
                        return False, {"success": False, "reason": "Failed condition (1): %s %s %s because %s" % (sourcevalue, condition["condition"]["value"], destinationvalue, check)}

                    #sourcevalue = sourcevalue.encode("utf-8")
                    sourcevalue = parse_wrapper_start(sourcevalue, self)
                    destinationvalue = condition["destination"]["value"]

                    check, destinationvalue, is_loop = parse_params(action, fullexecution, condition["destination"], self)
                    if check:
                        return False, {"success": False, "reason": "Failed condition (2): %s %s %s because %s" % (sourcevalue, condition["condition"]["value"], destinationvalue, check)}

                    #destinationvalue = destinationvalue.encode("utf-8")
                    destinationvalue = parse_wrapper_start(destinationvalue, self)
                    available_checks = [
                        "=",
                        "equals",
                        "!=",
                        "does not equal",
                        ">",
                        "larger than",
                        "<",
                        "less than",
                        ">=",
                        "<=",
                        "startswith",
                        "endswith",
                        "contains",
                        "contains_any_of",
                        "re",
                        "matches regex",
                    ]

                    if not condition["condition"]["value"] in available_checks:
                        self.logger.warning("Skipping %s %s %s because %s is invalid." % (sourcevalue, condition["condition"]["value"], destinationvalue, condition["condition"]["value"]))
                        continue

                    #self.logger.info(destinationvalue)
                    # NEGATE 

                    # Configuration = negated because of WorkflowAppActionParam..
                    validation = run_validation(sourcevalue, condition["condition"]["value"], destinationvalue)
                    try:
                        if condition["condition"]["configuration"]:
                            validation = not validation
                    except KeyError:
                        pass

                    if not validation:
                        self.logger.info("Failed condition check for %s %s %s." % (sourcevalue, condition["condition"]["value"], destinationvalue))
                        return False, {"success": False, "reason": "Failed condition (3): %s %s %s" % (sourcevalue, condition["condition"]["value"], destinationvalue)}


                # Make a general parser here, at least to get param["name"] = param["value"] in maparameter[string]string
                #for condition in branch.conditons:
    
            return True, ""



        # THE START IS ACTUALLY RIGHT HERE :O
        # Checks whether conditions are met, otherwise set 
        branchcheck, tmpresult = check_branch_conditions(action, fullexecution, self)
        if isinstance(tmpresult, object) or isinstance(tmpresult, list):
            self.logger.info("[DEBUG] Fixing branch return as object -> string")
            try:
                #tmpresult = tmpresult.replace("'", "\"")
                tmpresult = json.dumps(tmpresult) 
            except json.decoder.JSONDecodeError as e:
                self.logger.info(f"[WARNING] Failed condition parsing {tmpresult} to string")

        if not branchcheck:
            self.logger.info("Failed one or more branch conditions.")
            self.action_result["result"] = tmpresult
            self.action_result["status"] = "SKIPPED"
            try:
                ret = requests.post("%s%s" % (self.base_url, stream_path), headers=headers, json=self.action_result)
                self.logger.info("Result: %d" % ret.status_code)
                if ret.status_code != 200:
                    self.logger.info(ret.text)
            except requests.exceptions.ConnectionError as e:
                self.logger.exception(e)

            self.logger.info("\n\n[DEBUG] RETURNING BECAUSE A BRANCH FAILED: %s\n\n" % tmpresult)
            return

        # Replace name cus there might be issues
        # Not doing lower() as there might be user-made functions
        actionname = action["name"]
        if " " in actionname:
            actionname.replace(" ", "_", -1) 


        #if action.generated:
        #    actionname = actionname.lower()

        # Runs the actual functions
        try:
            func = getattr(self, actionname, None)
            if func == None:
                self.logger.debug(f"Failed executing {actionname} because func is None.")
                self.action_result["status"] = "FAILURE" 
                self.action_result["result"] = "Function %s doesn't exist." % actionname
            elif callable(func):
                try:
                    if len(action["parameters"]) < 1:
                        result = await func()
                    else:
                        # Potentially parse JSON here
                        # FIXME - add potential authentication as first parameter(s) here
                        # params[parameter["name"]] = parameter["value"]
                        #self.logger.info(fullexecution["authentication"]
                        # What variables are necessary here tho hmm

                        params = {}
                        try:
                            for item in action["authentication"]:
                                #self.logger.info("AUTH: ", key, value)
                                params[item["key"]] = item["value"]
                        except KeyError:
                            self.logger.info("[DEBUG] No authentication specified!")
                            pass
                                #action["authentication"] 

                        # Fixes OpenAPI body parameters for later.
                        newparams = []
                        counter = -1
                        bodyindex = -1
                        for parameter in action["parameters"]:
                            counter += 1

                            # Hack for key:value in options using ||
                            try:
                                if parameter["options"] != None and len(parameter["options"]) > 0:
                                    #self.logger.info(f'OPTIONS: {parameter["options"]}')
                                    #self.logger.info(f'OPTIONS VAL: {parameter}')
                                    if "||" in parameter["value"]:
                                        splitvalue = parameter["value"].split("||")
                                        if len(splitvalue) > 1:
                                            #self.logger.info(f'[INFO] Parsed split || options of actions["parameters"]["name"]')
                                            action["parameters"][counter]["value"] = splitvalue[1]

                            except (IndexError, KeyError, TypeError) as e:
                                self.logger.info("[WARNING] Options err: {e}")

                            # This part is purely for OpenAPI accessibility. 
                            # It replaces the data back into the main item
                            # Earlier, we handled each of the items and did later string replacement, 
                            # but this has changed to do lists within items and such
                            if parameter["name"] == "body": 
                                bodyindex = counter
                                #self.logger.info("PARAM: %s" % parameter)
                                try:
                                    values = parameter["value_replace"]
                                    if values != None:
                                        added = 0
                                        for val in values:
                                            replace_value = val["value"]
                                            replace_key = val["key"]
                                            if (val["value"].startswith("{") and val["value"].endswith("}")) or (val["value"].startswith("[") and val["value"].endswith("]")):
                                                self.logger.info(f"""Trying to parse as JSON: {val["value"]}""")
                                                try:
                                                    value_replace = json.loads(val["value"])
                                                    # If it gets here, remove the "" infront and behind the key as well since this is preventing the JSON from being loaded
                                                    replace_key = f"\"{replace_key}\""
                                                except json.decoder.JSONDecodeError as e:
                                                    self.logger.info("Failed JSON replacement for OpenAPI %s", val["key"])
                                            elif val["value"].lower() == "true" or val["value"].lower() == "false":
                                                replace_key = f"\"{replace_key}\""

                                            action["parameters"][counter]["value"] = action["parameters"][counter]["value"].replace(replace_key, replace_value, 1)

                                            self.logger.info(f'[INFO] Added param {val["key"]} for body (using OpenAPI)')
                                            added += 1

                                        #action["parameters"]["body"]

                                        self.logger.info("ADDED %d parameters for body" % added)
                                except KeyError as e:
                                    self.logger.info("KeyError body OpenAPI: %s" % e)
                                    pass

                                 
                                self.logger.info(f"""HANDLING {action["parameters"][counter]["value"]}""")
                                action["parameters"][counter]["value"] = recurse_cleanup_script(action["parameters"][counter]["value"])

                        #self.logger.info(action["parameters"])

                        # This seems redundant now 
                        self.logger.info("[DEBUG] Pre parameters")
                        for parameter in newparams:
                            action["parameters"].append(parameter)

                        self.action = action

                        # Setting due to them being overwritten, but still later useful
                        try:
                            self.original_action = json.loads(json.dumps(action))
                        except Exception as e:
                            self.logger.info(f"[ERROR] Failed parsing action as JSON to original action. This COULD have bad effects on LOOPED executions: {e}")

                        # calltimes is used to handle forloops in the app itself.
                        # 2 kinds of loop - one in gui with one app each, and one like this,
                        # which is super fast, but has a bad overview (potentially good tho)
                        calltimes = 1
                        result = ""

                        all_executions = []

                        # Multi_parameter has the data for each. variable
                        minlength = 0
                        self.logger.info("[DEBUG] Pre-loading parameters")
                        multi_parameters = json.loads(json.dumps(params))
                        multiexecution = False
                        multi_execution_lists = []
                        remove_params = []
                        for parameter in action["parameters"]:
                            check, value, is_loop = parse_params(action, fullexecution, parameter, self)
                            if check:
                                raise "Value check error: %s" % Exception(check)

                            # Custom format for ${name[0,1,2,...]}$
                            #submatch = "([${]{2}([0-9a-zA-Z_-]+)(\[.*\])[}$]{2})"
                            #self.logger.info(f"Returnedvalue: {value}")
                            # OLD: Used until 13.03.2021: submatch = "([${]{2}#?([0-9a-zA-Z_-]+)#?(\[.*\])[}$]{2})"
                            # \${[0-9a-zA-Z_-]+#?(\[.*?]}\$)
                            submatch = "([${]{2}#?([0-9a-zA-Z_-]+)#?(\[.*?]}\$))"
                            actualitem = re.findall(submatch, value, re.MULTILINE)
                            try:
                                if action["skip_multicheck"]:
                                    self.logger.info("Skipping multicheck")
                                    actualitem = []
                            except KeyError:
                                pass

                            #self.logger.info("Return value: %s" % value)
                            actionname = action["name"]
                            #self.logger.info("Multicheck ", actualitem)
                            #self.logger.info("ITEM LENGTH: %d, Actual item: %s" % (len(actualitem), actualitem))
                            if len(actualitem) > 0:
                                multiexecution = True

                                # Loop WITHOUT JSON variables go here. 
                                # Loop WITH variables go in else.
                                self.logger.info("Before first part in multiexec!")
                                handled = False

                                # Has a loop without a variable used inside
                                if len(actualitem[0]) > 2 and actualitem[0][1] == "SHUFFLE_NO_SPLITTER":

                                    self.logger.info("(1) Pre replacement: %s" % actualitem[0][2])
                                    tmpitem = value

                                    index = 0
                                    replacement = actualitem[index][2]
                                    if replacement.endswith("}$"):
                                        replacement = replacement[:-2]

                                    if replacement.startswith("\"") and replacement.endswith("\""):
                                        replacement = replacement[1:len(replacement)-1]

                                    self.logger.info("POST replacement: %s" % replacement)

                                    #json_replacement = tmpitem.replace(actualitem[index][0], replacement, 1)
                                    #self.logger.info("AFTER POST replacement: %s" % json_replacement)
                                    json_replacement = replacement
                                    try:
                                        json_replacement = json.loads(replacement)
                                    except json.decoder.JSONDecodeError as e:
                                        try:
                                            replacement = replacement.replace("\'", "\"", -1)
                                            json_replacement = json.loads(replacement)
                                        except:
                                            self.logger.info("JSON error singular: %s" % e)

                                    if len(json_replacement) > minlength:
                                        minlength = len(json_replacement)

                                    self.logger.info("PRE new_replacement")
                                    
                                    new_replacement = []
                                    for i in range(len(json_replacement)):
                                        if isinstance(json_replacement[i], dict) or isinstance(json_replacement[i], list):
                                            tmp_replacer = json.dumps(json_replacement[i])
                                            newvalue = tmpitem.replace(str(actualitem[index][0]), str(tmp_replacer), 1)
                                        else:
                                            newvalue = tmpitem.replace(str(actualitem[index][0]), str(json_replacement[i]), 1)

                                        try:
                                            newvalue = json.loads(newvalue)
                                        except json.decoder.JSONDecodeError as e:
                                            self.logger.info("DECODER ERROR: %s" % e)
                                            pass

                                        new_replacement.append(newvalue)

                                    self.logger.info("New replacement: %s" % new_replacement)

                                    # New
                                    tmpitem = tmpitem.replace(actualitem[index][0], replacement, 1)

                                    # This code handles files.
                                    resultarray = []
                                    isfile = False
                                    try:
                                        self.logger.info("(1) ------------ PARAM: %s" % parameter["schema"]["type"])
                                        if parameter["schema"]["type"] == "file" and len(value) > 0:
                                            self.logger.info("(1) SHOULD HANDLE FILE IN MULTI. Get based on value %s" % tmpitem) 
                                            # This is silly :)
                                            # Q: Is there something wrong with the download system?
                                            # It seems to return "FILE CONTENT: %s" with the ID as %s
                                            for tmp_file_split in json.loads(tmpitem):
                                                self.logger.info("(1) PRE GET FILE %s" % tmp_file_split)
                                                file_value = self.get_file(tmp_file_split)
                                                self.logger.info("(1) POST AWAIT %s" % file_value)
                                                resultarray.append(file_value)
                                                self.logger.info("(1) FILE VALUE FOR VAL %s: %s" % (tmp_file_split, file_value))

                                            isfile = True
                                    except NameError as e:
                                        self.logger.info("(1) SCHEMA NAMEERROR IN FILE HANDLING: %s" % e)
                                    except KeyError as e:
                                        self.logger.info("(1) SCHEMA KEYERROR IN FILE HANDLING: %s" % e)
                                    except json.decoder.JSONDecodeError as e:
                                        self.logger.info("(1) JSON ERROR IN FILE HANDLING: %s" % e)

                                    if not isfile:
                                        self.logger.info("Resultarray (NOT FILE): %s" % resultarray)
                                        params[parameter["name"]] = tmpitem
                                        multi_parameters[parameter["name"]] = new_replacement 
                                    else:
                                        self.logger.info("Resultarray (FILE): %s" % resultarray)
                                        params[parameter["name"]] = resultarray 
                                        multi_parameters[parameter["name"]] = resultarray 

                                    #if len(resultarray) == 0:
                                    #    self.logger.info("[WARNING] Returning empty array because the array length to be looped is 0 (1)")
                                    #    action_result["status"] = "SUCCESS" 
                                    #    action_result["result"] = "[]"
                                    #    self.send_result(action_result, headers, stream_path)
                                    #    return

                                    multi_execution_lists.append(new_replacement)
                                    #self.logger.info("MULTI finished: %s" % json_replacement)
                                else:
                                    self.logger.info(f"(2) Pre replacement (loop with variables). Variables: {actualitem}") #% actualitem)
                                    # This is here to handle for loops within variables.. kindof
                                    # 1. Find the length of the longest array
                                    # 2. Build an array with the base values based on parameter["value"] 
                                    # 3. Get the n'th value of the generated list from values
                                    # 4. Execute all n answers 
                                    replacements = {}
                                    curminlength = 0
                                    for replace in actualitem:
                                        try:
                                            to_be_replaced = replace[0]
                                            actualitem = replace[2]
                                            if actualitem.endswith("}$"):
                                                actualitem = actualitem[:-2]

                                        except IndexError:
                                            self.logger.info("[WARNING] Indexerror")
                                            continue

                                        #self.logger.info(f"\n\nTMPITEM: {actualitem}\n\n")
                                        #actualitem = parse_wrapper_start(actualitem)
                                        #self.logger.info(f"\n\nTMPITEM2: {actualitem}\n\n")

                                        try:
                                            itemlist = json.loads(actualitem)
                                            if len(itemlist) > minlength:
                                                minlength = len(itemlist)

                                            if len(itemlist) > curminlength:
                                                curminlength = len(itemlist)
                                            
                                        except json.decoder.JSONDecodeError as e:
                                            self.logger.info("JSON Error (replace): %s in %s" % (e, actualitem))

                                        replacements[to_be_replaced] = actualitem


                                    # Parses the data as string with length, split etc. before moving on. 


                                    #self.logger.info("In second part of else: %s" % (len(itemlist)))
                                    # This is a result array for JUST this value.. 
                                    # What if there are more?
                                    resultarray = []
                                    for i in range(0, curminlength): 
                                        tmpitem = json.loads(json.dumps(parameter["value"]))
                                        for key, value in replacements.items():
                                            replacement = json.dumps(json.loads(value)[i])
                                            if replacement.startswith("\"") and replacement.endswith("\""):
                                                replacement = replacement[1:len(replacement)-1]
                                            #except json.decoder.JSONDecodeError as e:

                                            #self.logger.info("REPLACING %s with %s" % (key, replacement))
                                            #replacement = parse_wrapper_start(replacement)
                                            tmpitem = tmpitem.replace(key, replacement, -1)


                                        # This code handles files.
                                        self.logger.info("(2) ------------ PARAM: %s" % parameter["schema"]["type"])
                                        isfile = False
                                        try:
                                            if parameter["schema"]["type"] == "file" and len(value) > 0:
                                                self.logger.info("(2) SHOULD HANDLE FILE IN MULTI. Get based on value %s" % parameter["value"]) 

                                                for tmp_file_split in json.loads(parameter["value"]):
                                                    self.logger.info("(2) PRE GET FILE %s" % tmp_file_split)
                                                    file_value = self.get_file(tmp_file_split)
                                                    self.logger.info("(2) POST AWAIT %s" % file_value)
                                                    resultarray.append(file_value)
                                                    self.logger.info("(2) FILE VALUE FOR VAL %s: %s" % (tmp_file_split, file_value))


                                                isfile = True
                                        except KeyError as e:
                                            self.logger.info("(2) SCHEMA ERROR IN FILE HANDLING: %s" % e)
                                        except json.decoder.JSONDecodeError as e:
                                            self.logger.info("(2) JSON ERROR IN FILE HANDLING: %s" % e)

                                        if not isfile:
                                            tmpitem = tmpitem.replace("\\\\", "\\", -1)
                                            resultarray.append(tmpitem)

                                    # With this parameter ready, add it to... a greater list of parameters. Rofl
                                    self.logger.info("LENGTH OF ARR: %d" % len(resultarray))
                                    if len(resultarray) == 0:
                                        self.logger.info("[WARNING] Returning empty array because the array length to be looped is 0 (0)")
                                        self.action_result["status"] = "SUCCESS" 
                                        self.action_result["result"] = "[]"
                                        self.send_result(self.action_result, headers, stream_path)
                                        return

                                    #self.logger.info("RESULTARRAY: %s" % resultarray)
                                    if resultarray not in multi_execution_lists:
                                        multi_execution_lists.append(resultarray)

                                    multi_parameters[parameter["name"]] = resultarray
                            else:
                                # Parses things like int(value)
                                self.logger.info("[DEBUG] Normal parsing (not looping)")#with data %s" % value)
                                # This part has fucked over so many random JSON usages because of weird paranthesis parsing

                                value = parse_wrapper_start(value, self)
                                self.logger.info("[DEBUG] Post return: %s" % value)

                                #self.logger.info("POST data value: %s" % value)
                                params[parameter["name"]] = value
                                multi_parameters[parameter["name"]] = value 

                                # This code handles files.
                                try:
                                    if parameter["schema"]["type"] == "file" and len(value) > 0:
                                        self.logger.info("\n SHOULD HANDLE FILE. Get based on value %s. <--- is this a valid ID?" % parameter["value"]) 
                                        file_value = self.get_file(value)
                                        self.logger.info("FILE VALUE: %s \n" % file_value)

                                        params[parameter["name"]] = file_value 
                                        multi_parameters[parameter["name"]] = file_value 
                                except KeyError as e:
                                    self.logger.info("SCHEMA ERROR IN FILE HANDLING: %s" % e)


                        #remove_params.append(parameter["name"])
                        # Fix lists here
                        # FIXME: This doesn't really do anything anymore
                        self.logger.info("[DEBUG] CHECKING multi execution list: %d!" % len(multi_execution_lists))
                        if len(multi_execution_lists) > 0:
                            self.logger.info("\n [DEBUG] Multi execution list has more data: %d" % len(multi_execution_lists))
                            filteredlist = []
                            for listitem in multi_execution_lists:
                                if listitem in filteredlist:
                                    continue

                                # FIXME: Subsub required?. Recursion! 
                                # Basically multiply what we have with the outer loop?
                                # 
                                #if isinstance(listitem, list):
                                #    for subitem in listitem:
                                #        filteredlist.append(subitem)
                                #else:
                                #    filteredlist.append(listitem)

                            #self.logger.info("New list length: %d" % len(filteredlist))
                            if len(filteredlist) > 1:
                                self.logger.info(f"Calculating new multi-loop length with {len(filteredlist)} lists")
                                tmplength = 1
                                for innerlist in filteredlist:
                                    tmplength = len(innerlist)*tmplength
                                    self.logger.info("List length: %d. %d*%d" % (tmplength, len(innerlist), tmplength))

                                minlength = tmplength

                                self.logger.info("New multi execution length: %d\n" % tmplength)

                        # Cleaning up extra list params
                        for subparam in remove_params:
                            #self.logger.info(f"DELETING {subparam}")
                            try:
                                del params[subparam]
                            except:
                                pass
                                #self.logger.info(f"Error with subparam deletion of {subparam} in {params}")
                            try:
                                del multi_parameters[subparam]
                            except:
                                #self.logger.info(f"Error with subparam deletion of {subparam} in {multi_parameters} (2)")
                                pass

                        #self.logger.info()
                        #self.logger.info(f"Param: {params}")
                        #self.logger.info(f"Multiparams: {multi_parameters}")
                        #self.logger.info()
                        
                        if not multiexecution:
                            # Runs a single iteration here
                            new_params = self.validate_unique_fields(params)
                            self.logger.info(f"[DEBUG] Returned with newparams of length {len(new_params)}")
                            if isinstance(new_params, list) and len(new_params) == 1:
                                params = new_params[0]
                            else:
                                self.logger.info("[WARNING] SHOULD STOP EXECUTION BECAUSE FIELDS AREN'T UNIQUE")
                                self.action_result["status"] = "SKIPPED"
                                self.action_result["result"] = f"A non-unique value was found"  
                                self.action_result["completed_at"] = int(time.time())
                                self.send_result(self.action_result, headers, stream_path)
                                return

                            self.logger.info("[INFO] Running normal execution (not loop)\n") 

                            #newres = await func(**params)
                            #self.logger.info("PARAMS: %s" % params)
                            #newres = ""
                            while True:
                                try:
                                    newres = await func(**params)
                                    break
                                except TypeError as e:
                                    newres = ""
                                    self.logger.info(f"[DEBUG] Got exec error: {errorstring}")
                                    errorstring = f"{e}"
                                    if "got an unexpected keyword argument" in errorstring:
                                        fieldsplit = errorstring.split("'")
                                        if len(fieldsplit) > 1:
                                            field = fieldsplit[1]
                            
                                            try:
                                                del params[field]
                                                self.logger.info("[WARNING] Removed field invalid field %s" % field)
                                            except KeyError:
                                                break
                                    else:
                                        raise e
                                        #break

                            self.logger.info("\n[INFO] Returned from execution with types %s" % type(newres))
                            #self.logger.info("\n[INFO] Returned from execution with %s of types %s" % (newres, type(newres)))#, newres)
                            if isinstance(newres, tuple):
                                self.logger.info("[INFO] Handling return as tuple")
                                # Handles files.
                                filedata = ""
                                file_ids = []
                                self.logger.info("TUPLE: %s" % newres[1])
                                if isinstance(newres[1], list):
                                    self.logger.info("[INFO] HANDLING LIST FROM RET")
                                    file_ids = self.set_files(newres[1])
                                elif isinstance(newres[1], object):
                                    self.logger.info("[INFO] Handling JSON from ret")
                                    file_ids = self.set_files([newres[1]])
                                elif isinstance(newres[1], str):
                                    self.logger.info("[INFO] Handling STRING from ret")
                                    file_ids = self.set_files([newres[1]])
                                else:
                                    self.logger.info("[INFO] NO FILES TO HANDLE")

                                tmp_result = {
                                    "result": newres[0], 
                                    "file_ids": file_ids
                                }
                                
                                result = json.dumps(tmp_result)
                            elif isinstance(newres, str):
                                self.logger.info("[INFO] Handling return as string of length %d" % len(newres))
                                result += newres
                            elif isinstance(newres, dict) or isinstance(newres, list):
                                try:
                                    result += json.dumps(newres, indent=4)
                                except json.JSONDecodeError as e:
                                    self.logger.info("Failed decoding result: %s" % e)

                                    try:
                                        result += str(newres)
                                    except ValueError:
                                        result += "Failed autocasting. Can't handle %s type from function. Must be string" % type(newres)
                                        self.logger.info("Can't handle type %s value from function" % (type(newres)))
                            else:
                                try:
                                    result += str(newres)
                                except ValueError:
                                    result += "Failed autocasting. Can't handle %s type from function. Must be string" % type(newres)
                                    self.logger.info("Can't handle type %s value from function" % (type(newres)))

                            self.logger.info("[INFO] POST NEWRES RESULT!")#, result)
                        else:
                            #self.logger.info("[INFO] APP_SDK DONE: Starting MULTI execution (length: %d) with values %s" % (minlength, multi_parameters))
                            # 1. Use number of executions based on the arrays being similar
                            # 2. Find the right value from the parsed multi_params

                            self.logger.info("[INFO] Running WITHOUT outer loop (looping)")
                            json_object = False
                            results = await self.run_recursed_items(func, multi_parameters, {})
                            if isinstance(results, dict) or isinstance(results, list):
                                json_object = True

                            #for i in range(0, minlength):
                            #    # To be able to use the results as a list:
                            #    self.logger.info("1: %s" % multi_parameters)
                            #    #baseparams = json.loads(json.dumps(multi_parameters))
                            #    baseparams = copy.deepcopy(multi_parameters)

                            #    self.logger.info("2: %s: %s" % (type(baseparams), baseparams))

                            #    self.logger.info("4")
                            #    self.logger.info("Running with params (1): %s" % baseparams) 

                            #    results = await self.run_recursed_items(func, baseparams, {})
                            #    if isinstance(results, dict) or isinstance(results, list):
                            #        json_object = True

                                # {'call': ['GoogleSafebrowsing_2_0', 'VirusTotal_GetReport_3_0']}
                                # 1. Check if list length is same as minlength
                                # 2. If NOT same length, duplicate based on length of array
                                # arraylength = 3 ["1", "2", "3"]
                                # arraylength = 4 ["1", "2", "3", "4"]
                                # minlength = 12 - 12/3 = 4 per item = ["1", "1", "1", "1", "2", "2", ...]

                                #try:
                                #    firstlist = True
                                #    for key, value in baseparams.items():
                                #        self.logger.info("Itemtype: %s" % type(value))
                                #        if isinstance(value, list):
                                #            try:
                                #                newvalue = value[i]
                                #            except IndexError:
                                #                pass

                                #            if len(value) != minlength and len(value) > 0:
                                #                newarray = []
                                #                self.logger.info("VALUE: ", value)
                                #                additiontime = minlength/len(value)
                                #                self.logger.info("Bad length for value: %d - should be %d. Additiontime: %d" % (len(value), minlength, additiontime))
                                #                if firstlist:
                                #                    self.logger.info("Running normal list (FIRST)")
                                #                    for subvalue in value:
                                #                        for number in range(int(additiontime)):
                                #                            newarray.append(subvalue)
                                #                else:
                                #                    #self.logger.info("Running secondary lists")
                                #                    ## 1. Set up length of array
                                #                    ## 2. Put values spread out
                                #                    # FIXME: This works well, except if lists are same length
                                #                    newarray = [""] * minlength

                                #                    cnt = 0
                                #                    for number in range(int(additiontime)):
                                #                        for subvaluerange in range(len(value)):
                                #                            # newlocation = number+(additiontime*subvaluerange)
                                #                            # self.logger.info("%d+(%d*%d) = %d. VAL: %s" % (number, additiontime, subvaluerange, newlocation, value[subvaluerange]))
                                #                            # Reverse if same length?
                                #                            if int(minlength/len(value)) == len(value):
                                #                                tmp = int(len(value)-subvaluerange-1)
                                #                                self.logger.info("NEW: %d" % tmp)
                                #                                newarray[cnt] = value[tmp] 
                                #                            else:
                                #                                newarray[cnt] = value[subvaluerange] 
                                #                            cnt += 1

                                #                #self.logger.info("Newarray =", newarray)
                                #                newvalue = newarray[i]
                                #                firstlist = False

                                #            baseparams[key] = newvalue

                                #    self.logger.info("3")
                                #except IndexError as e:
                                #    self.logger.info("IndexError: %s" % e)
                                #    baseparams[key] = "IndexError: %s" % e
                                #except KeyError as e:
                                #    self.logger.info("KeyError: %s" % e)
                                #    baseparams[key] = "KeyError: %s" % e
                                #self.logger.info("4")
                                #self.logger.info("Running with params (1): %s" % baseparams) 

                                #results = await self.run_recursed_items(func, baseparams, {})
                                #if isinstance(results, dict) or isinstance(results, list):
                                #    json_object = True

                                # Check the structure here. If "isloop", try to recurse?
                                # ret, is_loop = recurse_json(innervalue, parsersplit[outercnt+1:])
                                #ret = await func(**baseparams)
                                #self.logger.info("Return from execution: %s" % ret)
                                #if ret == None:
                                #    results.append("")
                                #    json_object = False
                                #elif isinstance(ret, dict) or isinstance(ret, list):
                                #    results.append(ret)
                                #    json_object = True
                                #else:
                                #    ret = ret.replace("\"", "\\\"", -1)

                                #    try:
                                #        results.append(json.loads(ret))
                                #        json_object = True
                                #    except json.decoder.JSONDecodeError as e:
                                #        #self.logger.info("Json: %s" % e)
                                #        results.append(ret)

                                #self.logger.info("Inner ret parsed: %s" % ret)

                            # Dump the result as a string of a list
                            #self.logger.info("RESULTS: %s" % results)
                            if isinstance(results, list) or isinstance(results, dict):
                                self.logger.info("JSON OBJECT? ", json_object)

                                # This part is weird lol
                                if json_object:
                                    try:
                                        result = json.dumps(results)
                                    except json.JSONDecodeError as e:
                                        self.logger.info(f"Failed to decode: {e}")
                                        result = results
                                else:
                                    result = "["
                                    for item in results:
                                        try:
                                            json.loads(item)
                                            result += item
                                        except json.decoder.JSONDecodeError as e:
                                            # Common nested issue which puts " around everything
                                            self.logger.info("Decodingerror: %s" % e)
                                            try:
                                                tmpitem = item.replace("\\\"", "\"", -1)
                                                json.loads(tmpitem)
                                                result += tmpitem

                                            except:
                                                result += "\"%s\"" % item

                                        result += ", "

                                    result = result[:-2]
                                    result += "]"
                            else:
                                self.logger.info("Normal result - no list?")
                                result = results

                    self.action_result["status"] = "SUCCESS" 
                    self.action_result["result"] = str(result)
                    if self.action_result["result"] == "":
                        self.action_result["result"] = result

                    self.logger.debug(f"[DEBUG] Executed {action['label']}-{action['id']}")#with result: {result}")
                    #self.logger.debug(f"Data: %s" % action_result)
                except TypeError as e:
                    self.logger.info("TypeError issue: %s" % e)
                    self.action_result["status"] = "FAILURE" 
                    self.action_result["result"] = "TypeError: %s" % str(e)
            else:
                self.logger.info("[DEBUG] Function %s doesn't exist?" % action["name"])
                self.logger.error(f"[ERROR] App {self.__class__.__name__}.{action['name']} is not callable")
                self.action_result["status"] = "FAILURE" 
                self.action_result["result"] = "Function %s is not callable." % actionname

        # https://ptb.discord.com/channels/747075026288902237/882017498550112286/882043773138382890
        except (requests.exceptions.RequestException, TimeoutError) as e:
            self.logger.info(f"Failed to execute request: {e}")
            self.logger.exception(f"Failed to execute {e}-{action['id']}")
            self.action_result["status"] = "SUCCESS" 
            try:
                self.action_result["result"] = json.dumps({
                    "success": False, 
                    "reason": f"Request error - failing silently. Details in detail section",
                    "details": f"{e}",
                })
            except json.decoder.JSONDecodeError as e:
                self.action_result["result"] = f"Request error: {e}"

        except Exception as e:
            self.logger.info(f"Failed to execute: {e}")
            self.logger.exception(f"Failed to execute {e}-{action['id']}")
            self.action_result["status"] = "FAILURE" 
            self.action_result["result"] = f"General exception: {e}" 

        self.action_result["completed_at"] = int(time.time())

        # Send the result :)
        self.send_result(self.action_result, headers, stream_path)
        return

    @classmethod
    async def run(cls, action=""):
        logging.basicConfig(format="{asctime} - {name} - {levelname}:{message}", style='{')
        logger = logging.getLogger(f"{cls.__name__}")
        logger.setLevel(logging.DEBUG)

        #self.logger.info("Started execution: %s!!" % cls)
        #self.logger.info("Action: %s" % action)
        #if isinstance(cls, object):
        #    self.action = cls

        app = cls(redis=None, logger=logger, console_logger=logger)
        if isinstance(action, str):
            print("[DEBUG] Normal execution. Action is a string.")
        elif isinstance(action, object):
            print("[DEBUG] OBJECT execution. Action is NOT a string.")
            app.action = action

            try:
                app.authorization = action["authorization"]
                app.current_execution_id = action["execution_id"]
            except:
                pass

            try:
                app.url = action["url"]
            except:
                pass

            try:
                app.base_url = action["base_url"]
            except:
                pass
        else:
            self.logger.info("ACTION TYPE (unhandled): %s" % type(action))

        await app.execute_action(app.action)
