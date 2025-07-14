from flask import Flask, request, jsonify
import requests
from urllib.parse import urlencode
import secrets
import base64
import hashlib
import json
import os

app = Flask(__name__)

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REDIRECT_URI = 'https://kick-overlay-production.up.railway.app'
HOST_URL = 'https://id.kick.com'
API_URL = 'https://api.kick.com'

@app.route('/')
def home():
    return '''
    <h2>Kick Webhook App</h2>
    <p><a href="/auth">Start OAuth</a></p>
    <p><a href="/webhook">Webhook Status</a></p>
    <p><a href="/setup">Setup</a></p>
    '''

@app.route('/auth')
def start_auth():
    global code_verifier, state
    
    # Generate PKCE values
    random_bytes = secrets.token_bytes(32)
    code_verifier = base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    state = secrets.token_urlsafe(32)
    
    params = {
        'response_type': 'code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'scope': 'user:read channel:read events:subscribe chat:write',
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'state': state
    }
    
    auth_url = f"{HOST_URL}/oauth/authorize?{urlencode(params)}"
    return f'''
    <h2>OAuth Authorization</h2>
    <p>Click the link below to authorize:</p>
    <a href="{auth_url}" target="_blank">{auth_url}</a>
    '''

# Store PKCE values globally (in production, use proper session management)
code_verifier = None
state = None
access_token = None

@app.route('/callback', methods=['GET', 'POST'])
def callback():
    global code_verifier, state, access_token
    
    if request.method == 'GET':
        # OAuth redirect from Kick
        code = request.args.get('code')
        returned_state = request.args.get('state')
        
        if not code:
            return "No authorization code received", 400
        
        if returned_state != state:
            return "State mismatch - possible CSRF attack", 400
        
        # Exchange code for tokens
        token_params = {
            'grant_type': 'authorization_code',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'code_verifier': code_verifier
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(f"{HOST_URL}/oauth/token", data=token_params, headers=headers)
            response.raise_for_status()
            tokens = response.json()
            access_token = tokens.get('access_token')
            
            return f'''
            <h2>✅ OAuth Success!</h2>
            <p><a href="/setup">Setup Overlays</a></p>
            '''
            
        except requests.exceptions.RequestException as e:
            return f"Token exchange failed: {str(e)}", 400
    
    else:  # POST method
        # Manual code entry (fallback)
        code = request.form.get('code')
        if not code:
            return "No code provided", 400
        
        return f"Manual code received: {code}<br>Please use the GET method for automatic token exchange."

@app.route('/setup')
def setup():
    global access_token, user_id
    
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    r = requests.get(f'{API_URL}/public/v1/channels', headers=headers)
    data = r.json()
    user_id = data['data'][0]['broadcaster_user_id']
    return f'''
    <p><a href=/subscribe>Subscribe to Webhook</a></p>
    <p><a href=/webhook>Monitor Chat</a></p>'''

@app.route('/subscribe')
def subscribe():
    global user_id, access_token
    
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    
    data = {
        'broadcaster_user_id': user_id,
        'events': [
            {
                'name': 'chat.message.sent',
                'version': 1
            }
        ],
        'method': 'webhook'
    }
    
    r = requests.post(f'{API_URL}/public/v1/events/subscriptions', headers=headers, json=data)
    print(f"Subscription response: {r.text}")
    
    return f'''
    <h2>Webhook Subscription Result</h2>
    <p><strong>Status Code:</strong> {r.status_code}</p>
    <p><strong>Response:</strong> {r.text}</p>
    <br>
    <p><a href="/webhook">Monitor Chat</a></p>
    '''

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    """Receive webhook data from Kick"""
    
    if request.method == 'POST':
        
        # Parse JSON data
        data = request.json or {}
        content = data['content']
        print(content)
        return 'ok'
    
    else:  # GET method
        # Show webhook status page
        return '''
        <h2>Webhook Status</h2>
        <p>✅ Webhook endpoint is active</p>
        <p>Check your console/terminal for incoming chat messages</p>
        <p>Send a message in your stream chat to test!</p>
        <br>
        <p><a href="/test-overlay">Test Overlay</a></p>
        '''

@app.route('/test-overlay')
def test_overlay():
    """Serve the test overlay HTML"""
    with open('M:/vscode_projects/kick/test_overlay.html', 'r') as f:
        content = f.read()
    
    # Add headers to skip ngrok browser warning
    response = app.response_class(content, mimetype='text/html')
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
