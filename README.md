# PSYCHE
### personality engine for friend groups

A local-first web app that maps your friend group's personalities, compares compatibility, and generates AI-powered analysis using MBTI data.

---

## Features

- **Profile Builder** — Add anyone with their MBTI type, variant (A/T), and trait scores across all 5 dimensions
- **Head-to-Head Compare** — Compatibility score per trait + AI analysis (synergy, clash, dynamic, verdict)
- **Group Analysis** — Pairwise compatibility grid for everyone + group dynamic AI breakdown
- **Role Suggestions** — Pick any subset + a context (startup, study group, project) → AI assigns ideal roles

## Stack

- Python / Flask
- Groq API (llama-3.1-8b-instant) for AI analysis
- JSONBin.io for persistent storage
- Vanilla JS + CSS — no frameworks

## Setup (local)

```bash
git clone https://github.com/vulgnox/Psyche_analyze.git
cd Psyche_analyze
pip install -r requirements.txt
export GROQ_API_KEY=your_key
export JSONBIN_KEY=your_key
export JSONBIN_ID=your_bin_id
python app.py
```

Open `http://localhost:5050`

## Deployed

Live at: https://psyche-analyze.onrender.com