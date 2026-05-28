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

# ─── Auth helpers ─────────────────────────────────────────────────────────────

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

MBTI_COMPAT_BONUS = {
    frozenset(["INTJ","ENFP"]): 18, frozenset(["INFJ","ENTP"]): 18,
    frozenset(["INTJ","ENTJ"]): 10, frozenset(["INTP","ENTP"]): 10,
    frozenset(["INFJ","INFP"]): 8,  frozenset(["ENFJ","ENFP"]): 8,
    frozenset(["ISTJ","ESFJ"]): 14, frozenset(["ISFJ","ESTJ"]): 14,
    frozenset(["ISTP","ESTP"]): 10, frozenset(["ISFP","ESFP"]): 10,
    frozenset(["ENTJ","INTP"]): 16, frozenset(["ENFJ","INFP"]): 16,
    frozenset(["ESTP","ISFJ"]): 12, frozenset(["ESFP","ISTJ"]): 12,
    frozenset(["INTJ","ESFP"]): -10, frozenset(["INTP","ESFJ"]): -10,
    frozenset(["INFJ","ESTP"]): -8,  frozenset(["INFP","ESTJ"]): -10,
}

TRAIT_WEIGHTS = {
    'intuitive': 1.4, 'thinking': 1.3,
    'introverted': 1.0, 'judging': 1.1, 'assertive': 0.8,
}

def compute_compatibility(a, b):
    scores = {}
    traits = ['introverted','intuitive','thinking','judging','assertive']
    weighted_total = 0.0
    weight_sum = 0.0
    for t in traits:
        diff = abs(a['traits'].get(t, 50) - b['traits'].get(t, 50))
        raw = max(0, 100 - (diff ** 1.25) * 0.9)
        scores[t] = round(raw)
        w = TRAIT_WEIGHTS[t]
        weighted_total += raw * w
        weight_sum += w
    overall = round(weighted_total / weight_sum)
    pair = frozenset([a['mbti'][:4], b['mbti'][:4]])
    bonus = MBTI_COMPAT_BONUS.get(pair, 0)
    if a['mbti'][:4] == b['mbti'][:4]:
        bonus += 6
    av = a.get('variant', 'A')
    bv = b.get('variant', 'A')
    if av == 'T' and bv == 'T':
        bonus -= 4
    elif av == 'A' and bv == 'A':
        bonus += 3
    overall = max(0, min(100, overall + bonus))
    return {"overall": overall, "by_trait": scores}

# ─── Enneagram data ───────────────────────────────────────────────────────────

ENNEA_TYPES = {
    1:"Reformer", 2:"Helper", 3:"Achiever", 4:"Individualist",
    5:"Investigator", 6:"Loyalist", 7:"Enthusiast", 8:"Challenger", 9:"Peacemaker"
}

ENNEA_WINGS = {
    1:[9,2], 2:[1,3], 3:[2,4], 4:[3,5],
    5:[4,6], 6:[5,7], 7:[6,8], 8:[7,9], 9:[8,1]
}

# Integration (growth) and disintegration (stress) directions
ENNEA_GROWTH = {1:7, 2:4, 3:6, 4:1, 5:8, 6:9, 7:5, 8:2, 9:3}
ENNEA_STRESS = {1:4, 2:8, 3:9, 4:2, 5:7, 6:3, 7:1, 8:5, 9:6}

ENNEA_CENTER = {
    'gut':   [8, 9, 1],   # instinctive — anger
    'heart': [2, 3, 4],   # feeling — shame
    'head':  [5, 6, 7],   # thinking — fear
}
CENTER_LABEL = {
    'gut':   'Gut / Instinctive',
    'heart': 'Heart / Feeling',
    'head':  'Head / Thinking',
}

# Type harmony matrix — bonuses (+) and friction penalties (-)
ENNEA_TYPE_COMPAT = {
    frozenset([1,7]): 15, frozenset([2,8]): 14, frozenset([3,6]): 12,
    frozenset([4,9]): 16, frozenset([5,8]): 13, frozenset([1,2]): 10,
    frozenset([3,9]): 11, frozenset([6,9]): 12, frozenset([7,5]): 10,
    frozenset([8,9]):  8, frozenset([1,6]):  8, frozenset([2,9]):  9,
    frozenset([4,2]): -8, frozenset([3,8]): -6, frozenset([7,4]): -10,
    frozenset([1,4]): -8, frozenset([2,3]): -6, frozenset([8,2]):  -5,
}

INSTINCT_COMPAT = {
    frozenset(['sp','sp']):  8, frozenset(['sx','sx']): 12,
    frozenset(['so','so']):  8, frozenset(['sp','sx']): -4,
    frozenset(['sp','so']):  2, frozenset(['sx','so']): -6,
}

def compute_enneagram_compatibility(a, b):
    ea = a.get('enneagram') or {}
    eb = b.get('enneagram') or {}
    if not ea.get('type') or not eb.get('type'):
        return None
    try:
        ta, tb = int(ea['type']), int(eb['type'])
    except (ValueError, TypeError):
        return None

    score = 65

    # Type harmony
    score += ENNEA_TYPE_COMPAT.get(frozenset([ta, tb]), 0)

    # Same type: good understanding, same blind spots
    if ta == tb:
        score += 5

    # Same center bonus (share the same emotional driver)
    for center_types in ENNEA_CENTER.values():
        if ta in center_types and tb in center_types:
            score += 4
            break

    # Growth arrow: healthy integration dynamic
    if ENNEA_GROWTH.get(ta) == tb or ENNEA_GROWTH.get(tb) == ta:
        score += 8
    # Stress arrow: destabilizing under pressure
    if ENNEA_STRESS.get(ta) == tb or ENNEA_STRESS.get(tb) == ta:
        score -= 5

    # Instinct variant
    ia = ea.get('instinct', '')
    ib = eb.get('instinct', '')
    if ia and ib:
        score += INSTINCT_COMPAT.get(frozenset([ia, ib]), 0)

    # Wing crossover: wing points toward partner's core type
    try:
        wa = int(str(ea.get('wing', '')).split('w')[-1])
        wb = int(str(eb.get('wing', '')).split('w')[-1])
        if wa == tb or wb == ta:
            score += 6
    except Exception:
        pass

    return max(0, min(100, round(score)))

def _ennea_center_of(t):
    for c, types in ENNEA_CENTER.items():
        if t in types:
            return c
    return ''

def _ennea_summary(f):
    ea = f.get('enneagram') or {}
    if not ea.get('type'):
        return ''
    parts = [f"Type {ea['type']} ({ENNEA_TYPES.get(int(ea['type']),'')})" ]
    if ea.get('wing'):   parts.append(f"Wing {ea['wing']}")
    if ea.get('instinct'): parts.append(f"Instinct {ea['instinct']}")
    if ea.get('tritype'): parts.append(f"Tritype {ea['tritype']}")
    return ', '.join(parts)

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
                "max_tokens": 450
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
    return list(get_col('friends').find({}, {'_id': 0}))

def save_friend(friend, owner_username):
    friend['owner'] = owner_username
    get_col('friends').replace_one(
        {'name': {'$regex': f'^{friend["name"]}$', '$options': 'i'}},
        friend, upsert=True
    )

def delete_friend_db(name, requester_username):
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
        else:
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
    ennea_compat = compute_enneagram_compatibility(a, b)
    ck = cache_key('compare', [a['name'], b['name']])

    ennea_line = ''
    if ennea_compat is not None:
        ennea_line = (
            f"\nEnneagram: {a['name']} = {_ennea_summary(a)} | "
            f"{b['name']} = {_ennea_summary(b)} | Enneagram compat: {ennea_compat}%"
        )

    prompt = (
        f"You are a personality analyst. Compare these two people briefly and brutally honestly.\n"
        f"{a['name']}: {a['mbti']}-{a.get('variant','')} — "
        f"I:{a['traits'].get('introverted',50)}% N:{a['traits'].get('intuitive',50)}% "
        f"T:{a['traits'].get('thinking',50)}% J:{a['traits'].get('judging',50)}% "
        f"A:{a['traits'].get('assertive',50)}%"
        f"{ennea_line}\n"
        f"{b['name']}: {b['mbti']}-{b.get('variant','')} — "
        f"I:{b['traits'].get('introverted',50)}% N:{b['traits'].get('intuitive',50)}% "
        f"T:{b['traits'].get('thinking',50)}% J:{b['traits'].get('judging',50)}% "
        f"A:{b['traits'].get('assertive',50)}%\n"
        f"MBTI Compatibility: {compat['overall']}%"
        + (f" | Enneagram Compatibility: {ennea_compat}%" if ennea_compat is not None else "") +
        "\nGive exactly 4 sections, each 1-2 sentences:\n"
        "1. SYNERGY: What they do best together\n"
        "2. CLASH: Where they will conflict\n"
        "3. DYNAMIC: How this relationship actually plays out\n"
        "4. VERDICT: One brutal honest line about this pairing\n"
        "Be direct. No fluff."
    )

    return jsonify({
        "a": a, "b": b,
        "compatibility": compat,
        "ennea_compat": ennea_compat,
        "analysis": groq_analyze(prompt, ck),
        "rarity_a": RARITY.get(a['mbti'][:4], 5),
        "rarity_b": RARITY.get(b['mbti'][:4], 5),
    })

@app.route('/api/group', methods=['GET'])
@login_required
def group_analysis():
    friends = load_friends()
    if len(friends) < 2:
        return jsonify({"error": "Need at least 2 friends"}), 400

    names = [f['name'] for f in friends]
    ck = cache_key('group', names)

    profiles = "\n".join([
        f"- {f['name']}: {f['mbti']}-{f.get('variant','')} ({MBTI_DESCRIPTIONS.get(f['mbti'][:4],'')})"
        + (f" | Ennea {_ennea_summary(f)}" if f.get('enneagram', {}).get('type') else '')
        for f in friends
    ])

    prompt = (
        f"You are a personality analyst. Analyze this group:\n{profiles}\n"
        "Give exactly 3 sections:\n"
        "1. GROUP DYNAMIC: How this group functions (2-3 sentences)\n"
        "2. ROLES: Who fills what role (1 line per person)\n"
        "3. WATCH OUT: One tension that could fracture this group (1-2 sentences)\n"
        "Be direct."
    )

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
        "friends": friends, "pairs": pairs,
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
    profiles = "\n".join([
        f"- {f['name']}: {f['mbti']}-{f.get('variant','')} ({MBTI_DESCRIPTIONS.get(f['mbti'][:4],'')})"
        + (f" | Ennea {_ennea_summary(f)}" if f.get('enneagram', {}).get('type') else '')
        for f in selected
    ])
    prompt = (
        f"Personality analyst. Context: {context}\nPeople:\n{profiles}\nGive:\n"
        "1. BEST ROLES: Ideal role for each person (1 line each)\n"
        "2. STRATEGY: How they should work together (2 sentences)\n"
        "3. RISK: What could go wrong (1 sentence)\n"
        "Brutally honest."
    )

    return jsonify({
        "analysis": groq_analyze(prompt, ck),
        "selected": selected
    })

# ─── Enneagram endpoints ──────────────────────────────────────────────────────

@app.route('/api/enneagram/compare', methods=['POST'])
@login_required
def enneagram_compare():
    body = request.json
    friends = load_friends()
    fm = {f['name'].lower(): f for f in friends}
    a = fm.get(body['a'].lower())
    b = fm.get(body['b'].lower())
    if not a or not b:
        return jsonify({"error": "Friend not found"}), 404

    ea = a.get('enneagram') or {}
    eb = b.get('enneagram') or {}
    if not ea.get('type') or not eb.get('type'):
        return jsonify({"error": "One or both profiles are missing Enneagram data. Add it in the profile."}), 400

    ta, tb = int(ea['type']), int(eb['type'])
    compat = compute_enneagram_compatibility(a, b)

    is_growth_ab = ENNEA_GROWTH.get(ta) == tb
    is_growth_ba = ENNEA_GROWTH.get(tb) == ta
    is_stress_ab = ENNEA_STRESS.get(ta) == tb
    is_stress_ba = ENNEA_STRESS.get(tb) == ta
    center_a = _ennea_center_of(ta)
    center_b = _ennea_center_of(tb)

    arrow_notes = []
    if is_growth_ab: arrow_notes.append(f"{a['name']} (Type {ta}) integrates toward Type {tb}")
    if is_growth_ba: arrow_notes.append(f"{b['name']} (Type {tb}) integrates toward Type {ta}")
    if is_stress_ab: arrow_notes.append(f"{a['name']} (Type {ta}) disintegrates toward Type {tb}")
    if is_stress_ba: arrow_notes.append(f"{b['name']} (Type {tb}) disintegrates toward Type {ta}")
    arrow_str = '; '.join(arrow_notes) if arrow_notes else 'no direct arrow connection'

    ck = cache_key('ennea_compare', [a['name'], b['name']])
    prompt = (
        f"Enneagram analyst. Compare these two people deeply.\n\n"
        f"{a['name']}: {_ennea_summary(a)}\n"
        f"{b['name']}: {_ennea_summary(b)}\n\n"
        f"Arrow dynamics: {arrow_str}\n"
        f"Centers: {a['name']} = {CENTER_LABEL.get(center_a,'?')} / {b['name']} = {CENTER_LABEL.get(center_b,'?')}\n"
        f"Enneagram compatibility: {compat}%\n\n"
        "Give exactly 4 sections (each 1-2 sentences):\n"
        "1. CORE DYNAMIC: What fundamentally drives this pairing\n"
        "2. GROWTH POTENTIAL: How they help each other develop\n"
        "3. SHADOW: Unspoken tensions and blind spots\n"
        "4. VERDICT: One brutally honest line about this pairing\n"
        "Be direct and precise. No fluff."
    )

    return jsonify({
        "a": a, "b": b,
        "ta": ta, "tb": tb,
        "compat": compat,
        "is_growth_ab": is_growth_ab, "is_growth_ba": is_growth_ba,
        "is_stress_ab": is_stress_ab, "is_stress_ba": is_stress_ba,
        "center_a": center_a, "center_b": center_b,
        "growth_of_a": ENNEA_GROWTH.get(ta), "stress_of_a": ENNEA_STRESS.get(ta),
        "growth_of_b": ENNEA_GROWTH.get(tb), "stress_of_b": ENNEA_STRESS.get(tb),
        "analysis": groq_analyze(prompt, ck)
    })

@app.route('/api/enneagram/group', methods=['GET'])
@login_required
def enneagram_group():
    friends = load_friends()
    with_ennea = [f for f in friends if (f.get('enneagram') or {}).get('type')]
    if len(with_ennea) < 2:
        return jsonify({"error": "Need at least 2 profiles with Enneagram data"}), 400

    names = [f['name'] for f in with_ennea]
    ck = cache_key('ennea_group', names)

    profiles = "\n".join([
        f"- {f['name']}: {_ennea_summary(f)}"
        for f in with_ennea
    ])

    # Center distribution
    centers = {'gut': [], 'heart': [], 'head': []}
    for f in with_ennea:
        t = int(f['enneagram']['type'])
        c = _ennea_center_of(t)
        if c:
            centers[c].append(f['name'])

    prompt = (
        f"Enneagram analyst. Analyze this group through the Enneagram lens.\n\n"
        f"{profiles}\n\n"
        f"Center distribution — Gut: {centers['gut']} | Heart: {centers['heart']} | Head: {centers['head']}\n\n"
        "Give exactly 3 sections:\n"
        "1. CENTER BALANCE: How gut/heart/head centers are distributed and what that means for this group\n"
        "2. ROLES: Natural Enneagram role each person plays (1 line each)\n"
        "3. GROWTH EDGE: The collective blind spot this group needs to watch\n"
        "Be direct. No flattery."
    )

    pairs = [
        {
            "a": with_ennea[i]['name'],
            "b": with_ennea[j]['name'],
            "ta": int(with_ennea[i]['enneagram']['type']),
            "tb": int(with_ennea[j]['enneagram']['type']),
            "score": compute_enneagram_compatibility(with_ennea[i], with_ennea[j]) or 0,
        }
        for i in range(len(with_ennea))
        for j in range(i + 1, len(with_ennea))
    ]

    return jsonify({
        "profiles": with_ennea,
        "pairs": pairs,
        "centers": {k: v for k, v in centers.items()},
        "analysis": groq_analyze(prompt, ck)
    })

# ─── Chemistry Map ────────────────────────────────────────────────────────────

@app.route('/api/chemistry', methods=['GET'])
@login_required
def chemistry():
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
            "ennea_type": (f.get('enneagram') or {}).get('type', ''),
            "ennea_name": ENNEA_TYPES.get(int((f.get('enneagram') or {}).get('type', 0) or 0), ''),
        }
        for f in friends
    ]

    edges = [
        {
            "a": friends[i]['name'],
            "b": friends[j]['name'],
            "score": compute_compatibility(friends[i], friends[j])['overall'],
            "ennea_score": compute_enneagram_compatibility(friends[i], friends[j]),
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