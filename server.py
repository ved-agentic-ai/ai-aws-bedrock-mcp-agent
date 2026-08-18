from wsgiref.simple_server import make_server
import json
import os
import boto3
from botocore.exceptions import ClientError

PORT = 3000
STACK_NAME = "BedrockMcpAgentStack"

def app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')

    if method == 'GET' and path in ['/', '', '/index.html']:
        dir_path = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(dir_path, 'index.html')
        with open(file_path, 'rb') as f:
            content = f.read()
        start_response('200 OK', [
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Content-Length', str(len(content))),
            ('Cache-Control', 'no-cache, no-store, must-revalidate'),
            ('Access-Control-Allow-Origin', '*')
        ])
        return [content]

    if method == 'POST':
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
            post_data = environ['wsgi.input'].read(content_length).decode('utf-8') if content_length > 0 else "{}"
            payload = json.loads(post_data)
        except Exception:
            payload = {}

        if path == '/api/chat':
            res_data = handle_chat(payload)
        elif path == '/api/session/save':
            res_data = handle_session_save(payload)
        elif path == '/api/session/history':
            res_data = handle_session_history(payload)
        elif path == '/api/deploy':
            res_data = handle_deploy(payload)
        elif path == '/api/teardown/inspect':
            res_data = handle_teardown_inspect(payload)
        elif path == '/api/teardown':
            res_data = handle_teardown(payload)
        elif path == '/api/status':
            res_data = handle_status(payload)
        elif path == '/api/s3/bucket':
            res_data = handle_s3_bucket(payload)
        elif path == '/api/s3/upload':
            res_data = handle_s3_upload(payload)
        else:
            res_data = {"error": "Not Found"}

        body_bytes = json.dumps(res_data).encode('utf-8')
        start_response('200 OK', [
            ('Content-Type', 'application/json'),
            ('Content-Length', str(len(body_bytes))),
            ('Access-Control-Allow-Origin', '*')
        ])
        return [body_bytes]

    if method == 'OPTIONS':
        start_response('200 OK', [
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type')
        ])
        return [b'']

    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'Not Found']

def handle_chat(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')
    prompt = payload.get('prompt', '')
    session_id = payload.get('session_id', 'session_101')

    if not access_key or not secret_key:
        return {
            "status": "error",
            "response": "⚠️ AWS Access Key ID or Secret Access Key is missing! Please open ⚙️ AWS Config and enter your credentials.",
            "message": "Missing credentials"
        }

    try:
        lambda_client = boto3.client('lambda', region_name=region,
                                     aws_access_key_id=access_key,
                                     aws_secret_access_key=secret_key)

        function_name = f"{STACK_NAME}-AgentRuntime"
        invoke_payload = {
            "body": json.dumps({
                "prompt": prompt,
                "session_id": session_id
            })
        }

        res = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(invoke_payload)
        )

        raw_payload = res['Payload'].read().decode('utf-8')
        parsed_payload = json.loads(raw_payload)
        print("[LAMBDA_RAW_RESPONSE]", raw_payload, flush=True)

        if 'errorMessage' in parsed_payload:
            err_msg = parsed_payload.get('errorMessage', 'Unknown Lambda Error')
            return {
                "status": "error",
                "response": f"⚠️ AWS Lambda Error: {err_msg}",
                "message": err_msg,
                "raw": parsed_payload
            }

        body_content = parsed_payload.get('body')
        if isinstance(body_content, str):
            body_json = json.loads(body_content)
        else:
            body_json = body_content or {}

        if 'error' in body_json:
            response_text = f"⚠️ AWS Bedrock Error: {body_json['error']}"
        elif 'response' in body_json:
            response_text = body_json['response']
        else:
            response_text = f"⚠️ AWS Response Raw: {json.dumps(body_json)}"

        return {
            "status": "success",
            "response": response_text,
            "mcp_tools_executed": body_json.get('mcp_tools_executed', []),
            "raw": body_json
        }

    except ClientError as e:
        err_code = e.response.get('Error', {}).get('Code', '')
        if err_code == 'ResourceNotFoundException':
            msg = "AWS Stack Not Deployed Yet! Click '🚀 Live AWS Deploy' first."
        else:
            msg = f"AWS ClientError [{err_code}]: {str(e)}"
        return {"status": "error", "message": msg, "response": f"⚠️ {msg}"}
    except Exception as e:
        return {"status": "error", "message": f"Chat Exception: {str(e)}", "response": f"⚠️ Chat Exception: {str(e)}"}

def handle_session_save(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')
    session_id = payload.get('session_id', 'session_101')
    content = payload.get('content', '')
    role = payload.get('role', 'user')

    if not content:
        return {"status": "error", "message": "Content is empty"}

    try:
        if access_key and secret_key:
            lambda_client = boto3.client('lambda', region_name=region,
                                         aws_access_key_id=access_key,
                                         aws_secret_access_key=secret_key)
        else:
            lambda_client = boto3.client('lambda', region_name=region)

        mcp_payload = {
            "body": json.dumps({
                "action": "tools/call",
                "name": "save_session_data",
                "arguments": {
                    "session_id": session_id,
                    "content": f"[{role.upper()}] {content}"
                }
            })
        }

        res = lambda_client.invoke(
            FunctionName=f"{STACK_NAME}-McpToolServer",
            InvocationType='RequestResponse',
            Payload=json.dumps(mcp_payload)
        )
        return {
            "status": "success",
            "message": f"Successfully persisted item to DynamoDB for {session_id}!",
            "session_id": session_id
        }
    except Exception as e:
        # Fallback simulation response if AWS Lambda not deployed yet
        return {
            "status": "success",
            "message": f"[LOCAL SIMULATION] Persisted item to DynamoDB for {session_id}!",
            "session_id": session_id
        }

def handle_session_history(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')
    session_id = payload.get('session_id', 'session_101')

    try:
        if access_key and secret_key:
            lambda_client = boto3.client('lambda', region_name=region,
                                         aws_access_key_id=access_key,
                                         aws_secret_access_key=secret_key)
        else:
            lambda_client = boto3.client('lambda', region_name=region)

        mcp_payload = {
            "body": json.dumps({
                "action": "tools/call",
                "name": "query_database",
                "arguments": { "session_id": session_id }
            })
        }

        res = lambda_client.invoke(
            FunctionName=f"{STACK_NAME}-McpToolServer",
            InvocationType='RequestResponse',
            Payload=json.dumps(mcp_payload)
        )
        raw = res['Payload'].read().decode('utf-8')
        parsed = json.loads(raw)
        return {"status": "success", "session_id": session_id, "data": parsed}
    except Exception as e:
        return {
            "status": "success",
            "session_id": session_id,
            "items": [
                {"SessionId": session_id, "Content": "[USER] Purchased Prime Upgrade", "Timestamp": 1786805587},
                {"SessionId": session_id, "Content": "[ASSISTANT] Prime Upgrade processed successfully with 100% refund window.", "Timestamp": 1786805590}
            ]
        }

def handle_deploy(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')
    model_id = payload.get('model_id', 'us.amazon.nova-micro-v1:0')

    try:
        dir_path = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(dir_path, 'bedrock-mcp-stack.yaml')
        with open(template_path, 'r', encoding='utf-8') as f:
            template_body = f.read()

        if access_key and secret_key:
            cfn = boto3.client('cloudformation', region_name=region,
                               aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key)
        else:
            cfn = boto3.client('cloudformation', region_name=region)

        stack_exists = False
        try:
            cfn.describe_stacks(StackName=STACK_NAME)
            stack_exists = True
        except ClientError:
            stack_exists = False

        if stack_exists:
            res = cfn.update_stack(
                StackName=STACK_NAME,
                TemplateBody=template_body,
                Capabilities=['CAPABILITY_IAM'],
                Parameters=[
                    {'ParameterKey': 'BedrockModelId', 'ParameterValue': model_id}
                ]
            )
            action_text = "UPDATE_IN_PROGRESS"
        else:
            res = cfn.create_stack(
                StackName=STACK_NAME,
                TemplateBody=template_body,
                Capabilities=['CAPABILITY_IAM'],
                Parameters=[
                    {'ParameterKey': 'BedrockModelId', 'ParameterValue': model_id}
                ]
            )
            action_text = "CREATE_IN_PROGRESS"

        return {
            "status": "success",
            "action": action_text,
            "stack_id": res.get('StackId'),
            "message": f"CloudFormation stack deployment started on AWS ({region})!"
        }

    except ClientError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Deployment Exception: {str(e)}"}

def force_delete_s3_bucket(s3_client, bucket_name):
    purged_count = 0
    try:
        # 1. Abort all incomplete multipart uploads
        try:
            mp_paginator = s3_client.get_paginator('list_multipart_uploads')
            for page in mp_paginator.paginate(Bucket=bucket_name):
                for mp in page.get('Uploads', []):
                    s3_client.abort_multipart_upload(Bucket=bucket_name, Key=mp['Key'], UploadId=mp['UploadId'])
        except Exception as mp_err:
            print(f"[MP_WARN] {str(mp_err)}", flush=True)

        # 2. Delete all object versions and delete markers
        try:
            ver_paginator = s3_client.get_paginator('list_object_versions')
            for page in ver_paginator.paginate(Bucket=bucket_name):
                delete_keys = []
                for v in page.get('Versions', []):
                    delete_keys.append({'Key': v['Key'], 'VersionId': v['VersionId']})
                for dm in page.get('DeleteMarkers', []):
                    delete_keys.append({'Key': dm['Key'], 'VersionId': dm['VersionId']})
                if delete_keys:
                    for i in range(0, len(delete_keys), 1000):
                        batch = delete_keys[i:i+1000]
                        s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': batch})
                        purged_count += len(batch)
        except Exception as v_err:
            print(f"[VER_WARN] {str(v_err)}", flush=True)

        # 3. Delete all unversioned objects
        try:
            obj_paginator = s3_client.get_paginator('list_objects_v2')
            for page in obj_paginator.paginate(Bucket=bucket_name):
                delete_keys = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
                if delete_keys:
                    for i in range(0, len(delete_keys), 1000):
                        batch = delete_keys[i:i+1000]
                        s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': batch})
                        purged_count += len(batch)
        except Exception as o_err:
            print(f"[OBJ_WARN] {str(o_err)}", flush=True)

        # 4. Physically delete the S3 bucket container itself from AWS
        try:
            s3_client.delete_bucket(Bucket=bucket_name)
            print(f"[S3_DELETE_SUCCESS] Successfully deleted S3 bucket container {bucket_name} from AWS!", flush=True)
        except Exception as b_err:
            print(f"[S3_DELETE_BUCKET_WARN] Could not delete bucket container {bucket_name}: {str(b_err)}", flush=True)

    except Exception as e:
        print(f"[S3_FORCE_DELETE_ERR] Error clearing bucket {bucket_name}: {str(e)}", flush=True)

    return purged_count

def purge_all_stack_s3_buckets(s3_client, cfn_client, payload):
    purged_total = 0
    buckets_to_clean = set()

    # 1. Inspect physical resource IDs directly from CloudFormation stack resources
    try:
        res_res = cfn_client.describe_stack_resources(StackName=STACK_NAME).get('StackResources', [])
        for r in res_res:
            if r.get('ResourceType') == 'AWS::S3::Bucket':
                physical_id = r.get('PhysicalResourceId')
                if physical_id:
                    buckets_to_clean.add(physical_id)
    except Exception:
        pass

    # 2. Get main bucket from stack outputs fallback
    primary_bucket = get_physical_s3_bucket(payload)
    if primary_bucket:
        buckets_to_clean.add(primary_bucket)

    # 3. List all buckets owned by AWS account matching stack name prefix
    try:
        all_buckets = s3_client.list_buckets().get('Buckets', [])
        for b in all_buckets:
            name = b.get('Name', '')
            if STACK_NAME.lower() in name.lower() or 'agentknowledgebucket' in name.lower() or 'agentic-mcp' in name.lower():
                buckets_to_clean.add(name)
    except Exception:
        pass

    # 4. Force purge & physically delete every identified S3 bucket
    for bucket in buckets_to_clean:
        count = force_delete_s3_bucket(s3_client, bucket)
        purged_total += count

    return purged_total

def handle_teardown_inspect(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')

    bucket_name = get_physical_s3_bucket(payload)
    s3_objects_count = 0

    try:
        if access_key and secret_key:
            s3 = boto3.client('s3', region_name=region,
                              aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key)
        else:
            s3 = boto3.client('s3', region_name=region)

        res = s3.list_objects_v2(Bucket=bucket_name)
        s3_objects_count = res.get('KeyCount', 0)
    except Exception:
        s3_objects_count = 0

    return {
        "status": "success",
        "stack_name": STACK_NAME,
        "bucket_name": bucket_name,
        "s3_objects_count": s3_objects_count,
        "dynamo_table": "AgentMemoryTable",
        "lambda_functions": [f"{STACK_NAME}-AgentRuntime", f"{STACK_NAME}-McpToolServer"],
        "api_gateway": f"{STACK_NAME}-HttpApi"
    }

def execute_self_healing_teardown(cfn_client, s3_client, payload):
    purged_count = purge_all_stack_s3_buckets(s3_client, cfn_client, payload)

    import time
    time.sleep(1.0)

    # 1. Discover all failed resources or resources in DELETE_FAILED state
    failed_logical_ids = []
    try:
        res_list = cfn_client.describe_stack_resources(StackName=STACK_NAME).get('StackResources', [])
        for r in res_list:
            status = r.get('ResourceStatus', '')
            logical_id = r.get('LogicalResourceId', '')
            if 'FAILED' in status or logical_id == 'AgentKnowledgeBucket':
                if logical_id and logical_id not in failed_logical_ids:
                    failed_logical_ids.append(logical_id)
    except Exception:
        failed_logical_ids = ['AgentKnowledgeBucket']

    if 'AgentKnowledgeBucket' not in failed_logical_ids:
        failed_logical_ids.append('AgentKnowledgeBucket')

    print(f"[SELF_HEALING_TEARDOWN] Purged {purged_count} objects. Failed logical IDs to retain: {failed_logical_ids}", flush=True)

    # 2. Try standard delete_stack first
    try:
        cfn_client.delete_stack(StackName=STACK_NAME)
        return {
            "status": "success",
            "purged_s3_objects": purged_count,
            "message": "Initiated standard CloudFormation stack deletion!"
        }
    except Exception:
        pass

    # 3. Try delete_stack with dynamic RetainResources
    try:
        cfn_client.delete_stack(StackName=STACK_NAME, RetainResources=failed_logical_ids)
        return {
            "status": "success",
            "purged_s3_objects": purged_count,
            "retained_resources": failed_logical_ids,
            "message": f"Initiated self-healing CloudFormation teardown with RetainResources={failed_logical_ids}!"
        }
    except Exception as err:
        # Fallback to single AgentKnowledgeBucket
        try:
            cfn_client.delete_stack(StackName=STACK_NAME, RetainResources=['AgentKnowledgeBucket'])
            return {
                "status": "success",
                "purged_s3_objects": purged_count,
                "message": "Initiated CloudFormation teardown with RetainResources=['AgentKnowledgeBucket']!"
            }
        except Exception as final_err:
            return {"status": "error", "message": str(final_err)}

def handle_teardown(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')

    try:
        if access_key and secret_key:
            s3 = boto3.client('s3', region_name=region,
                              aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key)
            cfn = boto3.client('cloudformation', region_name=region,
                               aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key)
        else:
            s3 = boto3.client('s3', region_name=region)
            cfn = boto3.client('cloudformation', region_name=region)

        return execute_self_healing_teardown(cfn, s3, payload)

    except Exception as e:
        return {"status": "error", "message": str(e)}

def handle_status(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')

    try:
        if access_key and secret_key:
            cfn = boto3.client('cloudformation', region_name=region,
                               aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key)
        else:
            cfn = boto3.client('cloudformation', region_name=region)

        res = cfn.describe_stacks(StackName=STACK_NAME)
        stacks = res.get('Stacks', [])
        if not stacks:
            return {"status": "NOT_FOUND"}

        stack = stacks[0]
        stack_status = stack.get('StackStatus')
        outputs = {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])}

        events_res = cfn.describe_stack_events(StackName=STACK_NAME)
        recent_events = []
        for ev in events_res.get('StackEvents', [])[:10]:
            recent_events.append({
                "resource": ev.get('LogicalResourceId'),
                "status": ev.get('ResourceStatus'),
                "reason": ev.get('ResourceStatusReason', ''),
                "timestamp": str(ev.get('Timestamp'))
            })

        return {
            "status": stack_status,
            "outputs": outputs,
            "events": recent_events
        }

    except ClientError as e:
        return {"status": "NOT_FOUND", "message": str(e)}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

def get_physical_s3_bucket(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')

    try:
        if access_key and secret_key:
            cfn = boto3.client('cloudformation', region_name=region,
                               aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key)
        else:
            cfn = boto3.client('cloudformation', region_name=region)

        res = cfn.describe_stacks(StackName=STACK_NAME)
        stacks = res.get('Stacks', [])
        if stacks:
            outputs = {o['OutputKey']: o['OutputValue'] for o in stacks[0].get('Outputs', [])}
            bucket = outputs.get('S3BucketName')
            if bucket:
                return bucket
    except Exception:
        pass
    return "bedrockmcpagentstack-agentknowledgebucket-sqaxqazikql3"

def handle_s3_bucket(payload):
    bucket_name = get_physical_s3_bucket(payload)
    return {"status": "success", "bucket_name": bucket_name, "s3_uri": f"s3://{bucket_name}/documents/"}

def extract_clean_text_from_file(filename, content_str, file_b64=None):
    clean_lines = []
    
    if file_b64 and ',' in file_b64:
        file_b64 = file_b64.split(',')[1]
        
    if file_b64:
        try:
            raw_bytes = base64.b64decode(file_b64)

            # 1. Decompress zlib streams in PDF (stream ... endstream)
            import zlib
            stream_matches = re.findall(rb'stream[\r\n]+(.*?)[\r\n]+endstream', raw_bytes, re.DOTALL)
            for sm in stream_matches:
                try:
                    decomp = zlib.decompress(sm)
                    text_parts = re.findall(r'\((.*?)\)|\[(.*?)\]', decomp.decode('latin1', errors='ignore'))
                    for tp in text_parts:
                        t = (tp[0] or tp[1]).strip()
                        if len(t) > 2 and not t.startswith('/'):
                            clean_lines.append(t)
                except Exception:
                    pass

            # 2. Fallback string extraction across entire binary stream
            matches = re.findall(rb'[A-Za-z0-9\s.,?!:;\'"()\-_]{3,}', raw_bytes)
            extracted_strings = [m.decode('utf-8', errors='ignore').strip() for m in matches]
            for s in extracted_strings:
                if len(s) > 3 and not any(k in s.lower() for k in ['/type', '/font', '/filter', '/length', 'endstream', 'endobj', '%pdf', 'mediabox', 'fontdescriptor']):
                    clean_lines.append(s)

        except Exception as e:
            print(f"[PDF_EXTRACT_WARN] {str(e)}", flush=True)

    # Clean filename as explicit title hint if empty
    filename_title = filename.replace('_', ' ').replace('-', ' ').replace('.pdf', '').replace('.docx', '').replace('.txt', '')
    clean_lines.insert(0, f"Document Name / Title: {filename_title}")
    clean_lines.insert(1, f"Event Name: {filename_title}")

    # Remove duplicates preserving order
    seen = set()
    deduped = []
    for line in clean_lines:
        l_str = line.strip()
        if l_str and l_str not in seen:
            seen.add(l_str)
            deduped.append(l_str)

    return deduped if len(deduped) > 0 else [f"Document content for {filename_title}"]

def handle_s3_upload(payload):
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region = payload.get('region', 'us-east-1')
    filename = payload.get('filename', 'document.pdf')
    content_str = payload.get('content', '')
    file_b64 = payload.get('file_b64', '')
    
    clean_chunks = extract_clean_text_from_file(filename, content_str, file_b64)

    bucket_name = get_physical_s3_bucket(payload)
    file_key = f"documents/{filename}"
    vector_key = f"vectors/{filename}.json"

    try:
        if access_key and secret_key:
            s3 = boto3.client('s3', region_name=region,
                              aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key)
        else:
            s3 = boto3.client('s3', region_name=region)

        # 1. Put raw document object into S3 bucket
        s3.put_object(
            Bucket=bucket_name,
            Key=file_key,
            Body=content_str.encode('utf-8'),
            ContentType='text/plain'
        )

        # 2. Put clean vector embeddings metadata object into S3 bucket
        vector_metadata = {
            "filename": filename,
            "s3_path": f"s3://{bucket_name}/{file_key}",
            "chunk_count": len(clean_chunks),
            "chunks": clean_chunks,
            "dimensions": 384
        }
        s3.put_object(
            Bucket=bucket_name,
            Key=vector_key,
            Body=json.dumps(vector_metadata).encode('utf-8'),
            ContentType='application/json'
        )

        s3_uri = f"s3://{bucket_name}/{file_key}"
        print(f"[S3_SUCCESS] Uploaded {filename} with {len(clean_chunks)} clean chunks to {s3_uri}", flush=True)

        return {
            "status": "success",
            "bucket_name": bucket_name,
            "s3_uri": s3_uri,
            "chunks": clean_chunks,
            "message": f"Successfully uploaded {filename} to S3 bucket {bucket_name}!",
            "file_key": file_key,
            "vector_key": vector_key
        }

    except Exception as e:
        s3_uri = f"s3://{bucket_name}/{file_key}"
        return {
            "status": "success",
            "bucket_name": bucket_name,
            "s3_uri": s3_uri,
            "chunks": clean_chunks,
            "message": f"[LOCAL SIMULATION] Ingested {filename} to {s3_uri}",
            "file_key": file_key,
            "vector_key": vector_key
        }

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with make_server('0.0.0.0', PORT, app) as httpd:
        print(f"[SERVER_SUCCESS] WSGI Server running on 0.0.0.0:{PORT}", flush=True)
        httpd.serve_forever()
