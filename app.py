from flask import Flask, render_template, request, jsonify, session, redirect
import os
import requests
import hashlib
import datetime
from functools import wraps
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'psyche-secret-2026')

MONGO_URI = os.environ.get('MONGO_URI')
_db = None

def get_db():
    global _db
    if _db is None:
        client = MongoClient(MONGO_URI)
        _db = client['psyche']
    return _db

def get_col(name):
    return get_db()[name]

# ─── Auth helpers ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def current_user():
    return session.get('user')

# ─── Cache helpers ────────────────────────────────────────────────────────────

def cache_key(analysis_type, names):
    """Hash of sorted names + analysis type."""
    key_str = analysis_type + ':' + ':'.join(sorted(n.lower() for n in names))
    return hashlib.sha256(key_str.encode()).hexdigest()

def get_cache(key):
    doc = get_col('cache').find_one({'key': key})
    if not doc:
        return None
    if datetime.datetime.utcnow() > doc['expires_at']:
        get_col('cache').delete_one({'key': key})
        return None
    return doc['value']

def set_cache(key, value):
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=15)
    get_col('cache').replace_one(
        {'key': key},
        {'key': key, 'value': value, 'expires_at': expires},
        upsert=True
    )

# ─── MBTI data ────────────────────────────────────────────────────────────────

MBTI_DESCRIPTIONS = {
    "INTJ":"Architect","INTP":"Logician","ENTJ":"Commander","ENTP":"Debater",
    "INFJ":"Advocate","INFP":"Mediator","ENFJ":"Protagonist","ENFP":"Campaigner",
    "ISTJ":"Logistician","ISFJ":"Defender","ESTJ":"Executive","ESFJ":"Consul",
    "ISTP":"Virtuoso","ISFP":"Adventurer","ESTP":"Entrepreneur","ESFP":"Entertainer",
}
RARITY = {
    "INTJ":2,"INFJ":2,"ENTJ":3,"ENFJ":3,"INTP":4,"INFP":5,"ENTP":4,"ENFP":7,
    "ISTJ":12,"ISFJ":13,"ESTJ":9,"ESFJ":12,"ISTP":5,"ISFP":6,"ESTP":5,"ESFP":7,
}

# MBTI cognitive function compatibility matrix (Golden Pair / Complement / Conflict)
# Based on cognitive function theory (Ni/Se, Ne/Si, Ti/Fe, Fi/Te dominant/aux pairings)
MBTI_COMPAT_BONUS = {
    frozenset(["INTJ","ENFP"]): 18, frozenset(["INFJ","ENTP"]): 18,
    frozenset(["INTJ","ENTJ"]): 10, frozenset(["INTP","ENTP"]): 10,
    frozenset(["INFJ","INFP"]): 8,  frozenset(["ENFJ","ENFP"]): 8,
    frozenset(["ISTJ","ESFJ"]): 14, frozenset(["ISFJ","ESTJ"]): 14,
    frozenset(["ISTP","ESTP"]): 10, frozenset(["ISFP","ESFP"]): 10,
    frozenset(["ENTJ","INTP"]): 16, frozenset(["ENFJ","INFP"]): 16,
    frozenset(["ESTP","ISFJ"]): 12, frozenset(["ESFP","ISTJ"]): 12,
    # Known friction pairs — penalty
    frozenset(["INTJ","ESFP"]): -10, frozenset(["INTP","ESFJ"]): -10,
    frozenset(["INFJ","ESTP"]): -8,  frozenset(["INFP","ESTJ"]): -10,
}

# Trait weights — not all traits matter equally for compatibility
TRAIT_WEIGHTS = {
    'intuitive': 1.4,   # N/S is the biggest predictor of communication clash
    'thinking':  1.3,   # T/F drives emotional friction
    'introverted': 1.0,
    'judging':   1.1,
    'assertive': 0.8,   # A/T matters least for interpersonal compat
}

def compute_compatibility(a, b):
    scores = {}
    traits = ['introverted','intuitive','thinking','judging','assertive']
    weighted_total = 0.0
    weight_sum = 0.0

    for t in traits:
        diff = abs(a['traits'].get(t, 50) - b['traits'].get(t, 50))
        # Non-linear curve: small diffs hurt less, large diffs hurt a lot more
        raw = max(0, 100 - (diff ** 1.25) * 0.9)
        scores[t] = round(raw)
        w = TRAIT_WEIGHTS[t]
        weighted_total += raw * w
        weight_sum += w

    overall = round(weighted_total / weight_sum)

    # MBTI cognitive function bonus/penalty
    pair = frozenset([a['mbti'][:4], b['mbti'][:4]])
    bonus = MBTI_COMPAT_BONUS.get(pair, 0)

    # Same type bonus (mirrors — understand each other but may clash on blind spots)
    if a['mbti'][:4] == b['mbti'][:4]:
        bonus += 6

    # Variant (A/T) interaction
    av = a.get('variant', 'A')
    bv = b.get('variant', 'A')
    if av == 'T' and bv == 'T':
        bonus -= 4   # Two turbulent types amplify stress
    elif av == 'A' and bv == 'A':
        bonus += 3   # Two assertives = stable baseline

    overall = max(0, min(100, overall + bonus))
    return {"overall": overall, "by_trait": scores}

# ─── Groq ─────────────────────────────────────────────────────────────────────

def groq_analyze(prompt, cache_key_val=None):
    if cache_key_val:
        cached = get_cache(cache_key_val)
        if cached:
            return cached

    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('GROQ_API_KEY')}"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400
            },
            timeout=15
        )
        result = res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Analysis unavailable: {str(e)}"

    if cache_key_val:
        set_cache(cache_key_val, result)

    return result

# ─── Friends helpers ──────────────────────────────────────────────────────────

def load_friends():
    """All friends visible to everyone (Option A: ownership tagged but all visible)."""
    return list(get_col('friends').find({}, {'_id': 0}))

def save_friend(friend, owner_username):
    friend['owner'] = owner_username
    get_col('friends').replace_one(
        {'name': {'$regex': f'^{friend["name"]}$', '$options': 'i'}},
        friend, upsert=True
    )

def delete_friend_db(name, requester_username):
    """Only owner can delete."""
    doc = get_col('friends').find_one({'name': {'$regex': f'^{name}$', '$options': 'i'}})
    if not doc:
        return False, "not found"
    if doc.get('owner') and doc['owner'] != requester_username:
        return False, "not owner"
    get_col('friends').delete_one({'name': {'$regex': f'^{name}$', '$options': 'i'}})
    return True, "ok"

# ─── Routes: Auth ─────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action', 'login')
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '').strip()

        if not username or not password:
            return render_template('login.html', error="username and password required", tab=action)

        users_col = get_col('users')

        if action == 'register':
            if users_col.find_one({'username': username}):
                return render_template('login.html', error="username already taken", tab='register')
            users_col.insert_one({
                'username': username,
                'password_hash': generate_password_hash(password),
                'created_at': datetime.datetime.utcnow()
            })
            session['user'] = username
            return redirect('/')

        else:  # login
            user = users_col.find_one({'username': username})
            if not user or not check_password_hash(user['password_hash'], password):
                return render_template('login.html', error="wrong username or password", tab='login')
            session['user'] = username
            return redirect('/')

    return render_template('login.html', error=None, tab='login')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ─── Routes: App ──────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return render_template('index.html',
        mbti_types=list(MBTI_DESCRIPTIONS.keys()),
        current_user=current_user()
    )

@app.route('/api/friends', methods=['GET'])
@login_required
def get_friends():
    return jsonify(load_friends())

@app.route('/api/friends', methods=['POST'])
@login_required
def add_friend():
    try:
        friend = request.json
        if not friend or not friend.get('name'):
            return jsonify({"status": "error", "message": "name required"}), 400
        save_friend(friend, current_user())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/friends/<name>', methods=['DELETE'])
@login_required
def delete_friend(name):
    ok, msg = delete_friend_db(name, current_user())
    if not ok:
        code = 403 if msg == "not owner" else 404
        return jsonify({"status": "error", "message": msg}), code
    return jsonify({"status": "ok"})

@app.route('/api/compare', methods=['POST'])
@login_required
def compare():
    body = request.json
    friends = load_friends()
    fm = {f['name'].lower(): f for f in friends}
    a = fm.get(body['a'].lower())
    b = fm.get(body['b'].lower())
    if not a or not b:
        return jsonify({"error": "Friend not found"}), 404

    compat = compute_compatibility(a, b)
    ck = cache_key('compare', [a['name'], b['name']])

    prompt = f"""You are a personality analyst. Compare these two people briefly and brutally honestly.
{a['name']}: {a['mbti']}-{a.get('variant','')} — I:{a['traits'].get('introverted',50)}% N:{a['traits'].get('intuitive',50)}% T:{a['traits'].get('thinking',50)}% J:{a['traits'].get('judging',50)}% A:{a['traits'].get('assertive',50)}%
{b['name']}: {b['mbti']}-{b.get('variant','')} — I:{b['traits'].get('introverted',50)}% N:{b['traits'].get('intuitive',50)}% T:{b['traits'].get('thinking',50)}% J:{b['traits'].get('judging',50)}% A:{b['traits'].get('assertive',50)}%
Compatibility: {compat['overall']}%
Give exactly 4 sections, each 1-2 sentences:
1. SYNERGY: What they do best together
2. CLASH: Where they will conflict
3. DYNAMIC: How this relationship actually plays out
4. VERDICT: One brutal honest line about this pairing
Be direct. No fluff."""

    return jsonify({
        "a": a, "b": b,
        "compatibility": compat,
        "analysis": groq_analyze(prompt, ck),
        "rarity_a": RARITY.get(a['mbti'][:4], 5),
        "rarity_b": RARITY.get(b['mbti'][:4], 5)
    })

@app.route('/api/group', methods=['GET'])
@login_required
def group_analysis():
    friends = load_friends()
    if len(friends) < 2:
        return jsonify({"error": "Need at least 2 friends"}), 400

    names = [f['name'] for f in friends]
    ck = cache_key('group', names)

    profiles = "\n".join([f"- {f['name']}: {f['mbti']}-{f.get('variant','')} ({MBTI_DESCRIPTIONS.get(f['mbti'][:4],'')})" for f in friends])
    prompt = f"""You are a personality analyst. Analyze this group:\n{profiles}\nGive exactly 3 sections:\n1. GROUP DYNAMIC: How this group functions (2-3 sentences)\n2. ROLES: Who fills what role (1 line per person)\n3. WATCH OUT: One tension that could fracture this group (1-2 sentences)\nBe direct."""

    pairs = [
        {
            "a": friends[i]['name'],
            "b": friends[j]['name'],
            "score": compute_compatibility(friends[i], friends[j])['overall']
        }
        for i in range(len(friends))
        for j in range(i + 1, len(friends))
    ]

    return jsonify({
        "friends": friends,
        "pairs": pairs,
        "analysis": groq_analyze(prompt, ck)
    })

@app.route('/api/suggest', methods=['POST'])
@login_required
def suggest():
    body = request.json
    names = body.get('names', [])
    context = body.get('context', 'working together on a project')
    fm = {f['name'].lower(): f for f in load_friends()}
    selected = [fm[n.lower()] for n in names if n.lower() in fm]
    if not selected:
        return jsonify({"error": "No valid friends selected"}), 400

    ck = cache_key(f'suggest:{context}', [f['name'] for f in selected])
    profiles = "\n".join([f"- {f['name']}: {f['mbti']}-{f.get('variant','')} ({MBTI_DESCRIPTIONS.get(f['mbti'][:4],'')})" for f in selected])
    prompt = f"""Personality analyst. Context: {context}\nPeople:\n{profiles}\nGive:\n1. BEST ROLES: Ideal role for each person (1 line each)\n2. STRATEGY: How they should work together (2 sentences)\n3. RISK: What could go wrong (1 sentence)\nBrutally honest."""

    return jsonify({
        "analysis": groq_analyze(prompt, ck),
        "selected": selected
    })

@app.route('/api/chemistry', methods=['GET'])
@login_required
def chemistry():
    """Return all nodes + edges for the chemistry map."""
    friends = load_friends()
    if len(friends) < 2:
        return jsonify({"error": "Need at least 2 profiles"}), 400

    nodes = [
        {
            "id": f['name'],
            "mbti": f['mbti'],
            "variant": f.get('variant', ''),
            "desc": MBTI_DESCRIPTIONS.get(f['mbti'][:4], ''),
            "owner": f.get('owner', ''),
        }
        for f in friends
    ]

    edges = [
        {
            "a": friends[i]['name'],
            "b": friends[j]['name'],
            "score": compute_compatibility(friends[i], friends[j])['overall']
        }
        for i in range(len(friends))
        for j in range(i + 1, len(friends))
    ]

    return jsonify({"nodes": nodes, "edges": edges})

@app.route('/api/health')
def health():
    try:
        friends = load_friends()
        return jsonify({"status": "ok", "storage": "mongodb", "profiles": len(friends)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5050)))