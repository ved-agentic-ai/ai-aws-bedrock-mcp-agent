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
        elif path == '/api/deploy':
            res_data = handle_deploy(payload)
        elif path == '/api/teardown':
            res_data = handle_teardown(payload)
        elif path == '/api/status':
            res_data = handle_status(payload)
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

def handle_teardown(payload):
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

        cfn.delete_stack(StackName=STACK_NAME)
        return {
            "status": "success",
            "message": "CloudFormation Stack deletion initiated on AWS. Teardown in progress."
        }

    except ClientError as e:
        return {"status": "error", "message": str(e)}
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

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with make_server('0.0.0.0', PORT, app) as httpd:
        print(f"[SERVER_SUCCESS] WSGI Server running on 0.0.0.0:{PORT}", flush=True)
        httpd.serve_forever()
