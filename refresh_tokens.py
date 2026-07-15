import os
import re
import subprocess

def get_token():
    print("Getting new access token via gcloud...")
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise Exception(f"Failed to get token: {result.stderr}")
    return result.stdout.strip()

def update_netrc(token):
    netrc_path = os.path.expanduser("~/.netrc")
    if not os.path.exists(netrc_path):
        print(f"No .netrc found at {netrc_path}")
        return
        
    print(f"Updating {netrc_path}...")
    with open(netrc_path, "r") as f:
        content = f.read()

    # We want to replace the password value following the login oauth2accesstoken line
    # For machine us-python.pkg.dev and machine us-go.pkg.dev
    # Example format:
    # machine us-python.pkg.dev
    #   login oauth2accesstoken
    #   password ya29...
    
    pattern = r"(machine\s+us-(?:python|go)\.pkg\.dev\s+login\s+oauth2accesstoken\s+password\s+)(\S+)"
    updated_content = re.sub(pattern, rf"\g<1>{token}", content)
    
    with open(netrc_path, "w") as f:
        f.write(updated_content)
    print("Updated .netrc")

def update_npmrc(token):
    npmrc_path = os.path.expanduser("~/.npmrc")
    if not os.path.exists(npmrc_path):
        print(f"No .npmrc found at {npmrc_path}")
        return
        
    print(f"Updating {npmrc_path}...")
    with open(npmrc_path, "r") as f:
        content = f.read()
        
    # Example format:
    # //us-npm.pkg.dev/...:_authToken=ya29...
    pattern = r"(_authToken=)(ya29\.\S+)"
    updated_content = re.sub(pattern, rf"\g<1>{token}", content)
    
    with open(npmrc_path, "w") as f:
        f.write(updated_content)
    print("Updated .npmrc")

if __name__ == "__main__":
    try:
        token = get_token()
        update_netrc(token)
        update_npmrc(token)
        print("Token refresh complete!")
    except Exception as e:
        print(f"Error: {e}")
