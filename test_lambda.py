import boto3
import json
import sys

# Read credentials from stdin or environment
ak = sys.argv[1] if len(sys.argv) > 1 else ""
sk = sys.argv[2] if len(sys.argv) > 2 else ""
region = sys.argv[3] if len(sys.argv) > 3 else "us-east-1"

print(f"Testing AWS Lambda invocation on region {region}...")

try:
    if ak and sk:
        l_client = boto3.client('lambda', region_name=region, aws_access_key_id=ak, aws_secret_access_key=sk)
    else:
        l_client = boto3.client('lambda', region_name=region)

    res = l_client.invoke(
        FunctionName="BedrockMcpAgentStack-AgentRuntime",
        InvocationType='RequestResponse',
        Payload=json.dumps({
            "body": json.dumps({
                "prompt": "Save user session for session_101: Purchased Prime Upgrade",
                "session_id": "session_101"
            })
        })
    )

    raw_payload = res['Payload'].read().decode('utf-8')
    print("--- RAW LAMBDA PAYLOAD ---")
    print(raw_payload)

except Exception as e:
    print("LAMBDA INVOKE EXCEPTION:", str(e))
