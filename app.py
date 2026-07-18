import streamlit as st
import fitz
import requests
import io
import re
import os
import json
from pathlib import Path

# Read API key from .env
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()

st.set_page_config(page_title="CV Optimizer IA", layout="centered")
st.title("📄 CV Optimizer IA")
st.info("🤖 Importe **plusieurs CV** → L'IA les fusionne → Colle une offre → **1 CV optimisé**")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Configuration")
    api_key = st.text_input("Clé API OpenRouter", type="password",
                            value=os.environ.get("OPENROUTER_API_KEY", ""))
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key
        st.success("✅ Clé configurée")
    else:
        st.warning("📌 [Obtenir une clé](https://openrouter.ai/keys)")
        st.stop()


# ---------- AI Call ----------
def call_ai(prompt: str, system: str = "") -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    import time
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "google/gemma-4-26b-a4b-it:free",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                },
                timeout=300,
            )
            if resp.status_code == 429:
                err_msg = resp.json().get("error",{}).get("message","429")
                wait = 30
                st.warning(f"⏳ tentative {attempt+1}/3 - {err_msg[:100]}. Attente {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            st.warning(f"⚠️ tentative {attempt+1}/3 : {e}")
            if attempt == 2:
                st.error(f"❌ Erreur API après 3 tentatives : {e}")
                st.stop()
            time.sleep(5)
    st.error("❌ Échec API : toutes les tentatives ont épuisé.")
    st.stop()


# ---------- UI ----------
uploaded_files = st.file_uploader("📤 Importer plusieurs CV (PDF)", type=["pdf"], accept_multiple_files=True)
jd_text = st.text_area("📋 Coller l'offre d'emploi", height=150,
                       placeholder="Colle ici le texte complet de l'offre...")

# ---------- Session state ----------
if "profile" not in st.session_state:
    st.session_state.profile = None
if "opt_data" not in st.session_state:
    st.session_state.opt_data = None
if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "jd_text" not in st.session_state:
    st.session_state.jd_text = None

# ---------- Extract & Optimize ----------
if st.button("🚀 Générer mon CV optimisé", type="primary", use_container_width=True):
    if not uploaded_files or not jd_text.strip():
        st.warning("⚠️ Importe au moins un CV ET colle une offre.")
        st.stop()

    # Clear previous state
    st.session_state.profile = None
    st.session_state.opt_data = None
    st.session_state.extracted = None

    # Step 1 : Extract text from all PDFs
    status = st.status("📖 Extraction des CV...", expanded=True)
    all_texts = []
    for f in uploaded_files:
        status.write(f"📄 {f.name} : lecture...")
        doc = fitz.open(stream=f.read(), filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        if text.strip():
            all_texts.append(f"--- {f.name} ---\n{text.strip()}")

    if not all_texts:
        st.error("❌ Aucun texte extrait des PDF.")
        st.stop()

    # Truncate each CV to avoid prompt overflow
    all_texts = [t[:8000] for t in all_texts]
    combined = "\n\n".join(all_texts)
    if len(combined) > 25000:
        combined = combined[:25000] + "\n\n[... suite tronquée pour la limite de l'API]"

    # Step 2 : Merge AND optimize in a single call
    status.write("🤖 Fusion des CV + optimisation pour l'offre... (1-2 min)")

    full_prompt = f"""Tu reçois plusieurs CV d'une même personne ET une offre d'emploi.
Fusionne TOUTES les informations de tous les CV en un profil complet, puis génère un CV optimisé pour l'offre.

Retourne UNIQUEMENT un JSON avec cette structure :
{{
  "personal_info": {{ "full_name": "", "email": "", "phone": "", "location": "", "title": "" }},
  "summary": "RÉSUMÉ OPTIMISÉ (4-5 lignes percutantes, riche en mots-clés de l'offre)",
  "skills": {{ "catégorie": ["compétence1", "compétence2", ...] }},
  "education": [{{ "institution": "", "degree": "", "field": "", "start_date": "", "end_date": "", "description": "description du diplôme si pertinent pour l'offre" }}],
  "experience": [
    {{ "company": "", "location": "", "position": "", "start_date": "", "end_date": "", "optimized_achievements": ["Réalisation CHIFFRÉE et adaptée à l'offre", ...] }}
  ],
  "certifications": [],
  "languages": [{{ "lang": "", "level": "" }}]
}}

RÈGLES STRICTES :
1. Fusionne TOUTES les infos de tous les CV sans rien perdre (diplômes, compétences, langues). Inclus TOUS les diplômes : Baccalauréat, Licence, Master, etc.
2. Pour les expériences : ne garde que les 2 ou 3 PLUS PERTINENTES pour l'offre. Ignore les expériences sans rapport.
3. Le summary doit être OPTIMISÉ pour l'offre : utilise ses mots-clés, montre l'impact
4. Chaque optimized_achievement doit contenir un CHIFFRE ou un RÉSULTAT MESURABLE (ex: "+30% de réussite", "encadré 15 formateurs", "formé 200 apprenants")
5. Garde les vraies expériences, n'invente RIEN
6. Compétences techniques : max 8 au total, choisis les PLUS PERTINENTES pour l'offre
7. FRANÇAIS uniquement, orthographe parfaite. AUCUN texte en langue Baoulé ou autres langues étrangères non demandées.
8. Education : le champ "degree" doit TOUJOURS inclure la discipline (ex: "Master en Biochimie", "Licence en Sciences de l'Éducation"). Ne mets pas juste "Master" ou "Licence".
9. Pour le plus haut diplôme (Master ou équivalent), ajoute une description (description) de 1-2 lignes expliquant le mémoire ou le projet principal si l'offre le requiert
10. Les noms d'entreprises, universités et lieux doivent être en format normal (première lettre de chaque mot en majuscule), PAS en majuscules
11. Format des dates : si le début et la fin sont dans la même année, écris "Mois - Mois Année" (ex: "Janvier - Décembre 2020"). Si années différentes, écris "Mois Année - Mois Année" (ex: "Janvier 2020 - Décembre 2022"). Utilise les mois en français.
12. Langues parlées : liste UNIQUEMENT les langues officielles (Français, Anglais, etc.). Supprime toute mention du Baoulé ou dialectes locaux.
13. Réponds STRICTEMENT en JSON, sans texte avant ni après

CVs à fusionner :
{combined}

Offre à cibler :
{jd_text}"""

    result_json = call_ai(full_prompt, "Tu es un assistant spécialisé en CV. Réponds UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ni après. Pas de ```json ni ```.")
    result_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", result_json, flags=re.MULTILINE).strip()
    # Try to extract JSON from the response if it's embedded in text
    json_match = re.search(r"\{.*\}", result_json, re.DOTALL)
    if json_match:
        result_json = json_match.group()
    try:
        profile = json.loads(result_json)
    except json.JSONDecodeError:
        st.error("❌ L'IA n'a pas retourné un JSON valide. Réponse brute :")
        st.code(result_json[:1500])
        st.info("💡 Relance la génération, l'IA peut parfois mal formater.")
        st.stop()

    # Store in session_state for editing step
    st.session_state.profile = profile
    st.session_state.opt_data = profile
    st.session_state.extracted = True
    st.rerun()

# ---------- Edit & Generate PDF ----------
if st.session_state.profile:
    profile = st.session_state.profile
    opt_data = st.session_state.opt_data

    st.success(f"✅ CV fusionné et optimisé !")

    # Show preview
    with st.expander("📋 Aperçu du CV généré", expanded=True):
        p = profile.get("personal_info", {})
        st.markdown(f"**{p.get('full_name','?')}** — {p.get('title','')} — {p.get('location','')}")
        st.markdown(f"**Résumé :** {profile.get('summary','')}")
        for cat, items in profile.get("skills", {}).items():
            if items:
                st.markdown(f"**{cat} :** {', '.join(items)}")

    # Let user edit personal info before PDF generation
    st.subheader("✏️ Vérifie et modifie tes informations")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Nom complet", value=p.get("full_name", p.get("name", "Candidat")), key="edit_name")
        email = st.text_input("Email", value=p.get("email", ""), key="edit_email")
    with col2:
        phone = st.text_input("Téléphone", value=p.get("phone", ""), key="edit_phone")
        location = st.text_input("Localisation", value=p.get("location", ""), key="edit_location")

    if not st.button("✅ Générer le PDF", use_container_width=True, type="primary", key="gen_pdf"):
        st.stop()
    status = st.status("📝 Génération du PDF...")

    import tempfile, os, subprocess, sys, base64
    from pathlib import Path

    summary = opt_data.get("summary", profile.get("summary", ""))
    skills = opt_data.get("skills", profile.get("skills", {}))
    experience = opt_data.get("experience", profile.get("experience", []))
    education = profile.get("education", [])
    languages = profile.get("languages", [])

    # ── Build HTML from template ──
    def esc_html(t):
        if not t: return ""
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

    def fmt_date(start, end):
        """Format dates: merge year if same (Janvier - Décembre 2020)"""
        if not start and not end: return ""
        if not start: return esc_html(end or "")
        if not end: return esc_html(start)
        import re
        sy = re.search(r"(\d{4})", start)
        ey = re.search(r"(\d{4})", end)
        if sy and ey and sy.group(1) == ey.group(1):
            start_clean = re.sub(r"\s*\d{4}", "", start).strip()
            return f"{esc_html(start_clean)} - {esc_html(end)}"
        return f"{esc_html(start)} - {esc_html(end)}"

    title_text = profile.get("personal_info", {}).get("title", "") or opt_data.get("title", "")

    # Experience HTML
    exp_html = ""
    for i, exp in enumerate(experience):
        pos = esc_html(exp.get("position", ""))
        co = esc_html(exp.get("company", ""))
        sd = exp.get("start_date", "")
        ed = exp.get("end_date", "")
        date_str = fmt_date(sd, ed)
        company_location = exp.get("location", "")
        company_line = f"{co} – {esc_html(company_location)}" if company_location else co

        bullets = ""
        for ach in exp.get("optimized_achievements", exp.get("achievements", [])):
            bullets += f'<li>{esc_html(ach)}</li>\n'

        exp_html += f"""
<div class="row">
  <div class="date-col">
    <div>• {date_str}</div>
    <div class="sub">{company_line}</div>
  </div>
  <div class="content">
    <h3>{pos}</h3>
    <ul>{bullets}</ul>
  </div>
</div>"""

    # Education HTML
    edu_html = ""
    for edu in education:
        deg = esc_html(edu.get("degree", ""))
        inst = esc_html(edu.get("institution", ""))
        sd = edu.get("start_date", "")
        ed = edu.get("end_date", "")
        date_str = fmt_date(sd, ed)
        desc = edu.get("description", "")
        desc_html = f'<div class="desc">{esc_html(desc)}</div>' if desc else ""
        deg_html = f'<h3>{deg}</h3>' if deg else ""
        edu_html += f"""
<div class="row">
  <div class="date-col">
    <div>• {date_str}</div>
    <div class="sub">{inst}</div>
  </div>
  <div class="content">
    {deg_html}
    {desc_html}
  </div>
</div>"""

    # Skills / Languages / Tools
    tool_cats = {"Outils", "Tools", "Technologies", "Logiciels", "Outils Numériques"}
    lang_list = [f"<li>{esc_html(l.get('lang',''))} : {esc_html(l.get('level',''))}</li>" for l in languages]
    tool_list = []
    skill_list = []
    for cat, items in skills.items():
        if items:
            if cat in tool_cats:
                tool_list.extend(f"<li>{esc_html(it)}</li>" for it in items)
            else:
                for it in items:
                    skill_list.append(f"<li>{esc_html(it)}</li>")

    if not lang_list: lang_list = ["<li>Français : Natif</li>"]
    if not tool_list: tool_list = ["<li>Suite MS Office</li>"]
    if not skill_list: skill_list = ["<li>Pédagogie</li>"]
    skill_list = skill_list[:8]  # max 8 compétences

    # Full HTML template (pure CSS, no Tailwind CDN — works with weasyprint)
    _html_tpl = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>CV - {{NAME}}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap" rel="stylesheet"/>
<style>
  @page { size: A4; margin: 0; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Montserrat', sans-serif; font-size: 13px; color: #757575; background: white; }
  .page { width: 210mm; min-height: 297mm; margin: 0 auto; padding: 15mm; }
  .name { font-size: 28px; font-weight: 700; letter-spacing: 0.15em; color: #2D2D2D; text-align: center; text-transform: uppercase; margin-bottom: 6px; }
  .title { font-size: 22px; font-weight: 300; color: #C5A059; text-align: center; margin-bottom: 20px; }
  .contact { text-align: center; font-size: 13px; margin-bottom: 24px; white-space: nowrap; }
  .contact span { margin: 0 12px; }
  .icon { color: #C5A059; }
  .summary { text-align: justify; line-height: 1.6; margin-bottom: 28px; }
  .section-title { color: #C5A059; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; font-size: 13px; border-bottom: 1px solid #C5A059; padding-bottom: 4px; margin-bottom: 12px; }
  .row { display: flex; margin-bottom: 20px; page-break-inside: avoid; }
  .date-col { width: 190px; flex-shrink: 0; color: #C5A059; font-weight: 600; font-size: 10px; text-transform: uppercase; }
  .date-col .sub { font-size: 10px; color: #757575; text-transform: capitalize; font-weight: 700; margin-top: 2px; }
  .content { flex-grow: 1; padding-left: 32px; }
  .content h3 { font-size: 10px; text-transform: uppercase; color: #2D2D2D; font-weight: 700; margin-bottom: 6px; }
  .content .desc { font-size: 12px; color: #666; margin-top: 4px; }
  ul { list-style: disc; padding-left: 18px; }
  li { margin-bottom: 3px; line-height: 1.4; }
  .bottom-section { display: flex; gap: 28px; page-break-inside: avoid; }
  .bottom-col { flex: 1; }
</style>
</head>
<body>
<div class="page">
  <div class="name">{{NAME}}</div>
  <div class="title">{{TITLE}}</div>
  <div class="contact">
    <span><span class="icon">&#9993;</span> {{EMAIL}}</span>
    <span><span class="icon">&#9742;</span> {{PHONE}}</span>
    <span><span class="icon">&#9782;</span> {{LOCATION}}</span>
  </div>
  <div class="summary">{{SUMMARY}}</div>

  <div class="section-title">EXP&#201;RIENCES PROFESSIONNELLES</div>
  {{EXP_HTML}}

  <div class="section-title">FORMATION</div>
  {{EDU_HTML}}

  <div class="bottom-section">
    <div class="bottom-col">
      <div class="section-title">LANGUES</div>
      <ul>{{LANG_LIST}}</ul>
    </div>
    <div class="bottom-col">
      <div class="section-title">COMP&#201;TENCES</div>
      <ul>{{SKILL_LIST}}</ul>
    </div>
    <div class="bottom-col">
      <div class="section-title">OUTILS</div>
      <ul>{{TOOL_LIST}}</ul>
    </div>
  </div>
</div>
</body>
</html>"""
    html = (_html_tpl.replace("{{NAME}}", esc_html(name.upper()))
            .replace("{{TITLE}}", esc_html(title_text))
            .replace("{{EMAIL}}", esc_html(email))
            .replace("{{PHONE}}", esc_html(phone))
            .replace("{{LOCATION}}", esc_html(location))
            .replace("{{SUMMARY}}", esc_html(summary))
            .replace("{{EXP_HTML}}", exp_html)
            .replace("{{EDU_HTML}}", edu_html)
            .replace("{{LANG_LIST}}", "".join(lang_list))
            .replace("{{SKILL_LIST}}", "".join(skill_list))
            .replace("{{TOOL_LIST}}", "".join(tool_list)))

    # ── Preview & PDF ──
    status.write("🖨️ Génération du PDF...")
    out_file = Path(tempfile.mktemp(suffix=".pdf"))

    is_linux = sys.platform.startswith("linux")

    if is_linux:
        from weasyprint import HTML
        HTML(string=html).write_pdf(out_file)
    else:
        # Windows: use Edge headless
        edge_candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        edge = next((e for e in edge_candidates if os.path.exists(e)), None)
        if not edge:
            st.error("❌ Aucun navigateur Edge ou Chromium trouvé pour la génération PDF.")
            st.stop()
        html_path = Path(tempfile.mktemp(suffix=".html"))
        html_path.write_text(html, encoding="utf-8")
        subprocess.run(
            [edge, "--headless", f"--print-to-pdf={out_file}", "--disable-gpu",
             "--no-first-run", f"file:///{html_path.as_posix()}"],
            capture_output=True, timeout=60,
        )

    if not out_file.exists():
        st.error("❌ Erreur génération PDF.")
        st.stop()

    status.update(label="✅ CV optimisé généré !", state="complete", expanded=False)
    clean_name = re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")

    # PDF preview via base64 data URI
    with open(out_file, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode()
    pdf_data_uri = f"data:application/pdf;base64,{pdf_b64}"
    st.markdown(
        f'<a href="{pdf_data_uri}" target="_blank" '
        f'style="display:block; text-align:center; padding:12px; background:#C5A059; '
        f'color:white; text-decoration:none; border-radius:6px; font-weight:700; '
        f'margin-bottom:12px;">&#128065; Aperçu du CV dans un nouvel onglet</a>',
        unsafe_allow_html=True,
    )

    with open(out_file, "rb") as f:
        st.download_button("📄 Télécharger le CV (PDF)", f, file_name=f"CV_{clean_name}.pdf",
                           mime="application/pdf", use_container_width=True)


