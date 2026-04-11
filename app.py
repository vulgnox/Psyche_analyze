from flask import Flask, render_template, request, jsonify
import json
import os
import requests

app = Flask(__name__)
DATA_FILE = os.path.join(os.path.dirname(__file__), 'data', 'friends.json')
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

MBTI_DESCRIPTIONS = {
    "INTJ": "Architect — strategic, independent, visionary",
    "INTP": "Logician — analytical, theoretical, flexible",
    "ENTJ": "Commander — decisive, ambitious, leader",
    "ENTP": "Debater — innovative, curious, argumentative",
    "INFJ": "Advocate — idealistic, empathic, purposeful",
    "INFP": "Mediator — creative, introspective, value-driven",
    "ENFJ": "Protagonist — charismatic, empathic, organized",
    "ENFP": "Campaigner — enthusiastic, creative, sociable",
    "ISTJ": "Logistician — responsible, practical, reliable",
    "ISFJ": "Defender — caring, loyal, detail-oriented",
    "ESTJ": "Executive — organized, assertive, traditional",
    "ESFJ": "Consul — social, caring, traditional",
    "ISTP": "Virtuoso — practical, observant, reserved",
    "ISFP": "Adventurer — gentle, artistic, spontaneous",
    "ESTP": "Entrepreneur — energetic, perceptive, bold",
    "ESFP": "Entertainer — spontaneous, energetic, fun-loving",
}

RARITY = {
    "INTJ": 2, "INFJ": 2, "ENTJ": 3, "ENFJ": 3,
    "INTP": 4, "INFP": 5, "ENTP": 4, "ENFP": 7,
    "ISTJ": 12, "ISFJ": 13, "ESTJ": 9, "ESFJ": 12,
    "ISTP": 5, "ISFP": 6, "ESTP": 5, "ESFP": 7,
}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"friends": []}
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def compute_compatibility(a, b):
    scores = {}
    traits = ['introverted', 'intuitive', 'thinking', 'judging', 'assertive']
    
    total = 0
    for t in traits:
        av = a['traits'].get(t, 50)
        bv = b['traits'].get(t, 50)
        diff = abs(av - bv)
        score = max(0, 100 - diff)
        scores[t] = round(score)
        total += score
    
    overall = round(total / len(traits))
    
    # same type bonus
    if a['mbti'][:2] == b['mbti'][:2]:
        overall = min(100, overall + 5)
    
    return {"overall": overall, "by_trait": scores}

def ollama_analyze(prompt):
    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {os.environ.get('GROQ_API_KEY')}"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400
            },
            timeout=15
        )
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Analysis unavailable: {str(e)}"
@app.route('/')
def index():
    return render_template('index.html', mbti_types=list(MBTI_DESCRIPTIONS.keys()))

@app.route('/api/friends', methods=['GET'])
def get_friends():
    data = load_data()
    return jsonify(data['friends'])

@app.route('/api/friends', methods=['POST'])
def add_friend():
    data = load_data()
    friend = request.json
    # remove existing with same name
    data['friends'] = [f for f in data['friends'] if f['name'].lower() != friend['name'].lower()]
    data['friends'].append(friend)
    save_data(data)
    return jsonify({"status": "ok"})

@app.route('/api/friends/<name>', methods=['DELETE'])
def delete_friend(name):
    data = load_data()
    data['friends'] = [f for f in data['friends'] if f['name'].lower() != name.lower()]
    save_data(data)
    return jsonify({"status": "ok"})

@app.route('/api/compare', methods=['POST'])
def compare():
    body = request.json
    data = load_data()
    friends = {f['name'].lower(): f for f in data['friends']}
    
    a = friends.get(body['a'].lower())
    b = friends.get(body['b'].lower())
    
    if not a or not b:
        return jsonify({"error": "Friend not found"}), 404
    
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

    analysis = ollama_analyze(prompt)
    
    return jsonify({
        "a": a,
        "b": b,
        "compatibility": compat,
        "analysis": analysis,
        "rarity_a": RARITY.get(a['mbti'][:4], 5),
        "rarity_b": RARITY.get(b['mbti'][:4], 5),
    })

@app.route('/api/group', methods=['GET'])
def group_analysis():
    data = load_data()
    friends = data['friends']
    if len(friends) < 2:
        return jsonify({"error": "Need at least 2 friends"}), 400
    
    profiles = "\n".join([
        f"- {f['name']}: {f['mbti']} ({f.get('variant','')})"
        for f in friends
    ])
    
    prompt = f"""You are a personality analyst. Analyze this group of people:

{profiles}

Give exactly 3 sections:
1. GROUP DYNAMIC: How this group functions as a whole (2-3 sentences)
2. ROLES: Who naturally fills what role in this group (1 line per person)
3. WATCH OUT: The one tension that could fracture this group (1-2 sentences)

Be direct and honest. No flattery."""

    analysis = ollama_analyze(prompt)
    
    # compute all pairwise compatibility
    pairs = []
    for i in range(len(friends)):
        for j in range(i+1, len(friends)):
            c = compute_compatibility(friends[i], friends[j])
            pairs.append({
                "a": friends[i]['name'],
                "b": friends[j]['name'],
                "score": c['overall']
            })
    
    return jsonify({
        "friends": friends,
        "pairs": pairs,
        "analysis": analysis
    })

@app.route('/api/suggest', methods=['POST'])
def suggest():
    body = request.json
    names = body.get('names', [])
    context = body.get('context', 'working together on a project')
    
    data = load_data()
    friends = {f['name'].lower(): f for f in data['friends']}
    
    selected = [friends[n.lower()] for n in names if n.lower() in friends]
    if not selected:
        return jsonify({"error": "No valid friends selected"}), 400
    
    profiles = "\n".join([
        f"- {f['name']}: {f['mbti']} ({f.get('variant','')})"
        for f in selected
    ])
    
    prompt = f"""Personality analyst. Context: {context}

People:
{profiles}

Give:
1. BEST ROLES: Ideal role for each person in this context (1 line each)
2. STRATEGY: How they should work together (2 sentences)
3. RISK: What could go wrong (1 sentence)

Brutally honest. No filler."""

    analysis = ollama_analyze(prompt)
    return jsonify({"analysis": analysis, "selected": selected})

if __name__ == '__main__':
    os.makedirs('data', exist_ok=True)
    print("\n  PSYCHE running at http://localhost:5050\n")
    app.run(debug=False, port=5050)
