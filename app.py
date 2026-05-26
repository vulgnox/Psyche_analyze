from flask import Flask, render_template, request, jsonify
import json
import os
import requests
from functools import wraps
from flask import session, redirect
from pymongo import MongoClient

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'psyche-secret-2026')
APP_PASSWORD = os.environ.get('APP_PASSWORD', 'psyche123')

MONGO_URI = os.environ.get('MONGO_URI')
_col = None

def get_collection():
    global _col
    if _col is None:
        client = MongoClient(MONGO_URI)
        _col = client['psyche']['friends']
    return _col

def load_data():
    try:
        friends = list(get_collection().find({}, {'_id': 0}))
        return {"friends": friends}
    except Exception as e:
        print(f"[ERROR] MongoDB load failed: {e}")
        return {"friends": []}

def save_friend(friend):
    get_collection().replace_one(
        {'name': {'$regex': f'^{friend["name"]}$', '$options': 'i'}},
        friend, upsert=True)

def delete_friend_db(name):
    get_collection().delete_one({'name': {'$regex': f'^{name}$', '$options': 'i'}})

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

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

def compute_compatibility(a, b):
    scores = {}
    traits = ['introverted','intuitive','thinking','judging','assertive']
    total = 0
    for t in traits:
        diff = abs(a['traits'].get(t,50) - b['traits'].get(t,50))
        scores[t] = round(max(0, 100 - diff))
        total += scores[t]
    overall = round(total / len(traits))
    if a['mbti'][:2] == b['mbti'][:2]:
        overall = min(100, overall + 5)
    return {"overall": overall, "by_trait": scores}

def ollama_analyze(prompt):
    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Content-Type":"application/json","Authorization":f"Bearer {os.environ.get('GROQ_API_KEY')}"},
            json={"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":prompt}],"max_tokens":400},
            timeout=15)
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Analysis unavailable: {str(e)}"

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password','').strip() == APP_PASSWORD.strip():
            session['logged_in'] = True
            return redirect('/')
        return render_template('login.html', error="wrong password")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/')
@login_required
def index():
    return render_template('index.html', mbti_types=list(MBTI_DESCRIPTIONS.keys()))

@app.route('/api/friends', methods=['GET'])
@login_required
def get_friends():
    return jsonify(load_data()['friends'])

@app.route('/api/friends', methods=['POST'])
@login_required
def add_friend():
    try:
        friend = request.json
        if not friend or not friend.get('name'):
            return jsonify({"status":"error","message":"name required"}), 400
        save_friend(friend)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.route('/api/friends/<name>', methods=['DELETE'])
@login_required
def delete_friend(name):
    try:
        delete_friend_db(name)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.route('/api/compare', methods=['POST'])
@login_required
def compare():
    body = request.json
    data = load_data()
    fm = {f['name'].lower(): f for f in data['friends']}
    a, b = fm.get(body['a'].lower()), fm.get(body['b'].lower())
    if not a or not b:
        return jsonify({"error":"Friend not found"}), 404
    compat = compute_compatibility(a, b)
    prompt = f"""You are a personality analyst. Compare these two people briefly and brutally honestly.
{a['name']}: {a['mbti']} ({a.get('variant','')}) — Introverted {a['traits'].get('introverted',50)}%, Intuitive {a['traits'].get('intuitive',50)}%, Thinking {a['traits'].get('thinking',50)}%, Judging {a['traits'].get('judging',50)}%, Assertive {a['traits'].get('assertive',50)}%
{b['name']}: {b['mbti']} ({b.get('variant','')}) — Introverted {b['traits'].get('introverted',50)}%, Intuitive {b['traits'].get('intuitive',50)}%, Thinking {b['traits'].get('thinking',50)}%, Judging {b['traits'].get('judging',50)}%, Assertive {b['traits'].get('assertive',50)}%
Compatibility score: {compat['overall']}%
Give exactly 4 sections, each 1-2 sentences:
1. SYNERGY: What they do best together
2. CLASH: Where they will conflict
3. DYNAMIC: How this relationship actually plays out
4. VERDICT: One brutal honest line about this pairing
Be direct. No fluff."""
    return jsonify({"a":a,"b":b,"compatibility":compat,"analysis":ollama_analyze(prompt),"rarity_a":RARITY.get(a['mbti'][:4],5),"rarity_b":RARITY.get(b['mbti'][:4],5)})

@app.route('/api/group', methods=['GET'])
@login_required
def group_analysis():
    friends = load_data()['friends']
    if len(friends) < 2:
        return jsonify({"error":"Need at least 2 friends"}), 400
    profiles = "\n".join([f"- {f['name']}: {f['mbti']} ({f.get('variant','')})" for f in friends])
    prompt = f"""You are a personality analyst. Analyze this group:\n{profiles}\nGive exactly 3 sections:\n1. GROUP DYNAMIC: How this group functions (2-3 sentences)\n2. ROLES: Who fills what role (1 line per person)\n3. WATCH OUT: One tension that could fracture this group (1-2 sentences)\nBe direct."""
    pairs = [{"a":friends[i]['name'],"b":friends[j]['name'],"score":compute_compatibility(friends[i],friends[j])['overall']} for i in range(len(friends)) for j in range(i+1,len(friends))]
    return jsonify({"friends":friends,"pairs":pairs,"analysis":ollama_analyze(prompt)})

@app.route('/api/suggest', methods=['POST'])
@login_required
def suggest():
    body = request.json
    names = body.get('names', [])
    context = body.get('context','working together on a project')
    fm = {f['name'].lower(): f for f in load_data()['friends']}
    selected = [fm[n.lower()] for n in names if n.lower() in fm]
    if not selected:
        return jsonify({"error":"No valid friends selected"}), 400
    profiles = "\n".join([f"- {f['name']}: {f['mbti']} ({f.get('variant','')})" for f in selected])
    prompt = f"""Personality analyst. Context: {context}\nPeople:\n{profiles}\nGive:\n1. BEST ROLES: Ideal role for each person (1 line each)\n2. STRATEGY: How they should work together (2 sentences)\n3. RISK: What could go wrong (1 sentence)\nBrutally honest."""
    return jsonify({"analysis":ollama_analyze(prompt),"selected":selected})

@app.route('/api/health')
def health():
    try:
        data = load_data()
        return jsonify({"status":"ok","storage":"mongodb","profiles":len(data.get('friends',[]))})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5050)))
