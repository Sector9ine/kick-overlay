from flask import Flask, request, jsonify
import requests
from urllib.parse import urlencode
from urllib.parse import urlparse
import secrets
import base64
import hashlib
import json
import os
import mysql.connector

def get_db_connection():
    url = os.environ['DATABASE_URL']
    # Example: mysql://user:pass@host:port/dbname
    parsed = urlparse(url)
    return mysql.connector.connect(
        host=parsed.hostname,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
        port=parsed.port or 3306
    )

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS calories (id INT PRIMARY KEY, value VARCHAR(255))')
    c.execute('INSERT IGNORE INTO calories (id, value) VALUES (1, "0")')
    conn.commit()
    conn.close()

app = Flask(__name__)

init_db()

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REDIRECT_URI = 'https://kick-overlay-production.up.railway.app/callback'
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
    global access_token, user_id, stream_owner
    
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    r = requests.get(f'{API_URL}/public/v1/channels', headers=headers)
    data = r.json()
    print(data)
    user_id = data['data'][0]['broadcaster_user_id']
    stream_owner = data['data'][0]['slug']
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

def is_moderator(sender):
    identity = sender.get('identity', {})
    badges = identity.get('badges', [])
    for badge in badges:
        if badge.get('type') == 'moderator':
            return True
    return False

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    global stream_owner
    
    if request.method == 'POST':   
        # Parse JSON data
        data = request.json or {}
        print(data)
        content = data['content']
        sender = data.get('sender', {})
        sender_username = sender.get('username', '')
        if (
            sender_username.lower() == stream_owner.lower() or is_moderator(sender)
        ) and content.startswith('!calories'):
            try:
                value = content.split(' ', 1)[1]
                if value.isdigit():
                    value = int(value)  # or int(value) if you only want integers

                conn = get_db_connection()
                c = conn.cursor()
                # Fetch the current value
                x = c.execute('SELECT value FROM calories WHERE id = 1')
                print(x)
                row = c.fetchone()
                current = int(row[0]) if row and row[0] is not None else 0

                new_total = current + value

                # Update the database with the new total
                c.execute('UPDATE calories SET value = %s WHERE id = 1', (str(new_total),))
                conn.commit()
                conn.close()
                print(f"Calories updated: {current} + {value} = {new_total}")
            except (IndexError, ValueError):
                print("Invalid or missing value for !calories command")
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
    
    print("CWD:", os.getcwd())
    print("Files:", os.listdir(os.getcwd()))
    with open('test_overlay.html', 'r') as f:
        content = f.read()
    
    # Add headers to skip ngrok browser warning
    response = app.response_class(content, mimetype='text/html')
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
