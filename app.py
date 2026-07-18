import streamlit as st
import fitz
import requests
import io
import re
import os
import json
import base64
import time
from pathlib import Path

# Read API key from .env
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()

st.set_page_config(page_title="CV Optimizer IA", layout="centered")
st.title("📄 CV Optimizer IA")
st.info("📁 **Tes CV** sont sauvegardés dans le cloud → colle une offre → **1 CV optimisé par IA**")

# ---------- Session state ----------
if "profile" not in st.session_state:
    st.session_state.profile = None
if "opt_data" not in st.session_state:
    st.session_state.opt_data = None
if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "jd_text" not in st.session_state:
    st.session_state.jd_text = None
if "cover_letter" not in st.session_state:
    st.session_state.cover_letter = None
if "source_texts" not in st.session_state:
    st.session_state.source_texts = []
if "sb_user" not in st.session_state:
    st.session_state.sb_user = None
if "sb_client" not in st.session_state:
    st.session_state.sb_client = None

# ---------- Cloud sync (Supabase Auth) ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "") or st.secrets.get("supabase", {}).get("url", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or st.secrets.get("supabase", {}).get("anon_key", "")
SB_OK = bool(SUPABASE_URL and SUPABASE_KEY)

if SB_OK and st.session_state.sb_client is None:
    from supabase import create_client
    st.session_state.sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)

def load_cloud():
    sb = st.session_state.sb_client
    u = st.session_state.sb_user
    if not sb or not u:
        return
    try:
        resp = sb.table("cv_texts").select("texts").eq("user_id", u.id).execute()
        if resp.data:
            st.session_state.source_texts = resp.data[0].get("texts", [])
    except Exception:
        pass

def save_cloud():
    sb = st.session_state.sb_client
    u = st.session_state.sb_user
    if not sb or not u:
        return
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sb.table("cv_texts").upsert(
            {"user_id": u.id, "texts": st.session_state.source_texts, "updated_at": now},
            on_conflict="user_id"
        ).execute()
    except Exception:
        pass

# Restore from URL on first load
if "profiles_init" not in st.session_state:
    tok_b64 = st.query_params.get("sb_token")
    if tok_b64 and SB_OK:
        try:
            tok = json.loads(base64.b64decode(tok_b64).decode("utf-8"))
            sb = st.session_state.sb_client
            sb.auth.set_session(tok["a"], tok["r"])
            r = sb.auth.get_user()
            if r and r.user:
                st.session_state.sb_user = r.user
                load_cloud()
        except Exception:
            pass
    st.session_state.profiles_init = True

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Configuration")
    api_key = st.text_input("Clé API OpenRouter", type="password",
                            value=os.environ.get("OPENROUTER_API_KEY", ""))
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key
    else:
        st.warning("📌 [Obtenir une clé](https://openrouter.ai/keys)")
        st.stop()
    cv_lang = st.selectbox("🌐 Langue du CV", ["Français", "English", "Español", "Português"], index=0)

    st.divider()

    # ---------- Auth ----------
    sb = st.session_state.sb_client
    sb_user = st.session_state.sb_user

    if SB_OK:
        if sb_user:
            st.success(f"✅ Connecté : **{sb_user.email}**")
            if st.button("🚪 Déconnexion", use_container_width=True):
                sb.auth.sign_out()
                st.session_state.sb_user = None
                st.session_state.source_texts = []
                st.query_params.pop("sb_token", None)
                st.rerun()

            st.divider()
            st.subheader("📄 Mes CV sources")

            # Status
            n = len(st.session_state.source_texts)
            st.caption(f"{n} CV source{'s' if n != 1 else ''} enregistré{'s' if n != 1 else ''}")

            # Add a CV
            extra_pdf = st.file_uploader("Ajouter un CV (PDF)", type=["pdf"], key="add_cv")
            if extra_pdf:
                doc = fitz.open(stream=extra_pdf.read(), filetype="pdf")
                text = "".join(page.get_text() for page in doc)
                doc.close()
                if text.strip():
                    st.session_state.source_texts.append(f"--- {extra_pdf.name} ---\n{text.strip()}")
                    save_cloud()
                    st.success(f"✅ {extra_pdf.name} ajouté")
                    st.rerun()
                else:
                    st.error("❌ Texte vide")

            if st.session_state.source_texts:
                for idx, txt in enumerate(st.session_state.source_texts):
                    name = txt.split("\n")[0].replace("--- ", "").replace(" ---", "").strip()
                    cols = st.columns([4, 1])
                    with cols[0]:
                        st.text(f"📄 {name or f'CV {idx+1}'}")
                    with cols[1]:
                        if st.button("✕", key=f"del_txt_{idx}"):
                            st.session_state.source_texts.pop(idx)
                            save_cloud()
                            st.rerun()

                if st.button("🗑️ Tout vider", use_container_width=True):
                    st.session_state.source_texts = []
                    save_cloud()
                    st.rerun()
        else:
            with st.expander("🔐 Connexion / Inscription", expanded=True):
                auth_email = st.text_input("Email", placeholder="ex: moi@email.com", key="auth_email")
                auth_pw = st.text_input("Mot de passe (6+ car.)", type="password", key="auth_pw")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🔑 Connexion", use_container_width=True):
                        if not auth_email or not auth_pw:
                            st.error("❌ Remplis tous les champs")
                        else:
                            try:
                                resp = sb.auth.sign_in_with_password({"email": auth_email, "password": auth_pw})
                                if resp and resp.user:
                                    st.session_state.sb_user = resp.user
                                    tok = {"a": resp.session.access_token, "r": resp.session.refresh_token}
                                    st.query_params["sb_token"] = base64.b64encode(json.dumps(tok).encode("utf-8")).decode("utf-8")
                                    load_cloud()
                                    st.rerun()
                            except Exception as e:
                                st.error(f"❌ {e}")
                with col2:
                    if st.button("📝 Inscription", use_container_width=True):
                        if not auth_email or not auth_pw:
                            st.error("❌ Remplis tous les champs")
                        else:
                            try:
                                resp = sb.auth.sign_up({"email": auth_email, "password": auth_pw})
                                if resp and resp.user:
                                    st.success("✅ Compte créé ! Vérifie tes emails pour confirmer.")
                                else:
                                    st.error("❌ Erreur lors de l'inscription.")
                            except Exception as e:
                                st.error(f"❌ {e}")
    else:
        st.info("ℹ️ Cloud non configuré. Ajoute Supabase dans les Secrets.")


# ---------- AI Call ----------
def call_ai(prompt: str, system: str = "") -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
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
has_stored = len(st.session_state.source_texts) > 0
uploaded_files = st.file_uploader(
    "📤 Importer plusieurs CV (PDF)" + (" — ou laisse vide pour utiliser les CV sauvegardés" if has_stored else ""),
    type=["pdf"], accept_multiple_files=True)
jd_text = st.text_area("📋 Coller l'offre d'emploi", height=150,
                       placeholder="Colle ici le texte complet de l'offre...")

gen_label = "🚀 Générer mon CV optimisé"
if has_stored and not uploaded_files:
    gen_label = "🚀 Regénérer avec nouvelle offre (CV sauvegardés)"

# ---------- Extract & Optimize ----------
if st.button(gen_label, type="primary", use_container_width=True):
    if not jd_text.strip():
        st.warning("⚠️ Colle une offre d'emploi.")
        st.stop()
    if not uploaded_files and not has_stored:
        st.warning("⚠️ Importe au moins un CV OU charge un profil sauvegardé.")
        st.stop()

    st.session_state.profile = None
    st.session_state.opt_data = None
    st.session_state.extracted = None
    st.session_state.cover_letter = None

    status = st.status("📖 **Extraction des CV...**", expanded=True)

    all_texts = list(st.session_state.source_texts)  # start with stored texts
    for i, f in enumerate(uploaded_files or []):
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

    all_texts = [t[:8000] for t in all_texts]
    st.session_state.source_texts = all_texts  # store for later re-use
    combined = "\n\n".join(all_texts)
    if len(combined) > 25000:
        combined = combined[:25000] + "\n\n[... suite tronquée pour la limite de l'API]"

    status.write("⏳ **Fusion + optimisation IA...**")

    lang_map = {"Français": "FRANÇAIS", "English": "ENGLISH", "Español": "ESPAÑOL", "Português": "PORTUGUÊS"}
    lang_rule = lang_map.get(cv_lang, "FRANÇAIS")

    full_prompt = f"""Tu reçois plusieurs CV d'une même personne ET une offre d'emploi.
Fusionne TOUTES les informations de tous les CV en un profil complet, puis génère un CV optimisé pour l'offre.

Retourne UNIQUEMENT un JSON avec cette structure :
{{{{
  "personal_info": {{ "full_name": "", "email": "", "phone": "", "location": "", "title": "" }},
  "summary": "RÉSUMÉ OPTIMISÉ (4-5 lignes percutantes, riche en mots-clés de l'offre)",
  "skills": {{ "catégorie": ["compétence1", "compétence2", ...] }},
  "education": [{{ "institution": "", "degree": "", "field": "", "start_date": "", "end_date": "", "description": "description du diplôme si pertinent pour l'offre" }}],
  "experience": [
    {{ "company": "", "location": "", "position": "", "start_date": "", "end_date": "", "optimized_achievements": ["Réalisation CHIFFRÉE et adaptée à l'offre", ...] }}
  ],
  "certifications": [],
  "languages": [{{ "lang": "", "level": "" }}]
}}}}

RÈGLES STRICTES :
1. Fusionne TOUTES les infos de tous les CV sans rien perdre (diplômes, compétences, langues). Inclus TOUS les diplômes : Baccalauréat, Licence, Master, etc.
2. Pour les expériences : ne garde que les 2 ou 3 PLUS PERTINENTES pour l'offre. Ignore les expériences sans rapport.
3. Le summary doit être OPTIMISÉ pour l'offre : utilise ses mots-clés, montre l'impact
4. Chaque optimized_achievement doit contenir un CHIFFRE ou un RÉSULTAT MESURABLE (ex: "+30% de réussite", "encadré 15 formateurs", "formé 200 apprenants")
5. Garde les vraies expériences, n'invente RIEN
6. Compétences techniques : max 8 au total, choisis les PLUS PERTINENTES pour l'offre
7. Langue du CV : {lang_rule} uniquement, orthographe parfaite. AUCUN texte en langue Baoulé ou autres langues étrangères non demandées.
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

    with st.spinner("🤖 Fusion IA en cours... (1-2 min)"):
        result_json = call_ai(full_prompt, "Tu es un assistant spécialisé en CV. Réponds UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ni après. Pas de ```json ni ```.")
    status.write("📋 **Analyse du résultat...**")
    result_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", result_json, flags=re.MULTILINE).strip()
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

    status.update(label="✅ **CV fusionné et optimisé !**", state="complete", expanded=False)

    save_cloud()

    st.session_state.profile = profile
    st.session_state.opt_data = profile
    st.session_state.extracted = True
    st.session_state.cv_lang = cv_lang
    st.session_state.jd_text = jd_text
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

    gen_pdf = st.button("✅ Générer le PDF", use_container_width=True, type="primary", key="gen_pdf")
    gen_cl = st.button("✍️ Générer la lettre de motivation", use_container_width=True, key="gen_cl")

    # ---------- PDF Generation ----------
    if gen_pdf:
        status = st.status("📝 Génération du PDF...")
        import tempfile, os, subprocess, sys, base64
        from pathlib import Path

        summary = opt_data.get("summary", profile.get("summary", ""))
        skills = opt_data.get("skills", profile.get("skills", {}))
        experience = sorted(opt_data.get("experience", profile.get("experience", [])),
                            key=lambda x: x.get("end_date", x.get("start_date", "")), reverse=True)
        education = sorted(profile.get("education", []),
                           key=lambda x: x.get("end_date", x.get("start_date", "")), reverse=True)
        languages = profile.get("languages", [])

        def esc_html(t):
            if not t: return ""
            return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

        def fmt_date(start, end):
            if not start and not end: return ""
            if not start: return esc_html(end or "")
            if not end: return esc_html(start)
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
            exp_html += f"""<div class="row">
  <div class="date-col">
    <div>\u2022 {date_str}</div>
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
            edu_html += f"""<div class="row">
  <div class="date-col">
    <div>\u2022 {date_str}</div>
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
        if not lang_list: lang_list = ["<li>Fran\u00e7ais : Natif</li>"]
        if not tool_list: tool_list = ["<li>Suite MS Office</li>"]
        if not skill_list: skill_list = ["<li>P\u00e9dagogie</li>"]
        skill_list = skill_list[:8]

        # Section titles by language
        lang_tpl = st.session_state.get("cv_lang", "Fran\u00e7ais")
        if lang_tpl == "English":
            sec_exp, sec_edu, sec_lang, sec_skills, sec_tools = "PROFESSIONAL EXPERIENCE", "EDUCATION", "LANGUAGES", "SKILLS", "TOOLS"
        elif lang_tpl == "Espa\u00f1ol":
            sec_exp, sec_edu, sec_lang, sec_skills, sec_tools = "EXPERIENCIA PROFESIONAL", "FORMACI\u00d3N", "IDIOMAS", "COMPETENCIAS", "HERRAMIENTAS"
        elif lang_tpl == "Portugu\u00eas":
            sec_exp, sec_edu, sec_lang, sec_skills, sec_tools = "EXPERI\u00caNCIA PROFISSIONAL", "FORMA\u00c7\u00c3O", "IDIOMAS", "COMPET\u00caNCIAS", "FERRAMENTAS"
        else:
            sec_exp, sec_edu, sec_lang, sec_skills, sec_tools = "EXP\u00c9RIENCES PROFESSIONNELLES", "FORMATION", "LANGUES", "COMP\u00c9TENCES", "OUTILS"

        # Build HTML
        _html_tpl = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>CV - {{NAME}}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap" rel="stylesheet"/>
<style>
  @page { size: A4; margin: 15mm; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Montserrat', sans-serif; font-size: 13px; color: #757575; background: white; }
  .page { width: 100%; min-height: 297mm; }
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
  .bottom-section { display: flex; gap: 28px; page-break-inside: avoid; margin-top: 8mm; }
  .bottom-col { flex: 1; }
</style>
</head>
<body>
<div class="page">
  <div style="page-break-inside: avoid;">
    <div class="name">{{NAME}}</div>
    <div class="title">{{TITLE}}</div>
    <div class="contact">
      <span><span class="icon">&#9993;</span> {{EMAIL}}</span>
      <span><span class="icon">&#9742;</span> {{PHONE}}</span>
      <span><span class="icon">&#9782;</span> {{LOCATION}}</span>
    </div>
    <div class="summary">{{SUMMARY}}</div>
    <div class="section-title">{{SEC_EXP}}</div>
    {{EXP_HTML}}
    <div class="section-title">{{SEC_EDU}}</div>
    {{EDU_HTML}}
  </div>
  <div class="bottom-section">
    <div class="bottom-col">
      <div class="section-title">{{SEC_LANG}}</div>
      <ul>{{LANG_LIST}}</ul>
    </div>
    <div class="bottom-col">
      <div class="section-title">{{SEC_SKILLS}}</div>
      <ul>{{SKILL_LIST}}</ul>
    </div>
    <div class="bottom-col">
      <div class="section-title">{{SEC_TOOLS}}</div>
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
                .replace("{{SEC_EXP}}", sec_exp)
                .replace("{{SEC_EDU}}", sec_edu)
                .replace("{{SEC_LANG}}", sec_lang)
                .replace("{{SEC_SKILLS}}", sec_skills)
                .replace("{{SEC_TOOLS}}", sec_tools)
                .replace("{{EXP_HTML}}", exp_html)
                .replace("{{EDU_HTML}}", edu_html)
                .replace("{{LANG_LIST}}", "".join(lang_list))
                .replace("{{SKILL_LIST}}", "".join(skill_list))
                .replace("{{TOOL_LIST}}", "".join(tool_list)))

        status.write("\U0001f5a8 G\u00e9n\u00e9ration du PDF...")
        out_file = Path(tempfile.mktemp(suffix=".pdf"))

        is_linux = sys.platform.startswith("linux")
        if is_linux:
            from weasyprint import HTML
            HTML(string=html).write_pdf(out_file)
        else:
            edge_candidates = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]
            edge = next((e for e in edge_candidates if os.path.exists(e)), None)
            if not edge:
                st.error("❌ Aucun navigateur Edge ou Chromium trouv\u00e9 pour la g\u00e9n\u00e9ration PDF.")
                st.stop()
            html_path = Path(tempfile.mktemp(suffix=".html"))
            html_path.write_text(html, encoding="utf-8")
            subprocess.run(
                [edge, "--headless", f"--print-to-pdf={out_file}", "--disable-gpu",
                 "--no-first-run", f"file:///{html_path.as_posix()}"],
                capture_output=True, timeout=60,
            )

        if not out_file.exists():
            st.error("❌ Erreur g\u00e9n\u00e9ration PDF.")
            st.stop()

        status.update(label="✅ CV optimis\u00e9 g\u00e9n\u00e9r\u00e9 !", state="complete", expanded=False)
        clean_name = re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")

        with open(out_file, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode()
        pdf_data_uri = f"data:application/pdf;base64,{pdf_b64}"
        st.markdown(
            f'<a href="{pdf_data_uri}" target="_blank" '
            f'style="display:block; text-align:center; padding:12px; background:#C5A059; '
            f'color:white; text-decoration:none; border-radius:6px; font-weight:700; '
            f'margin-bottom:12px;">&#128065; Aper\u00e7u du CV dans un nouvel onglet</a>',
            unsafe_allow_html=True,
        )

        with open(out_file, "rb") as f:
            st.download_button("📄 T\u00e9l\u00e9charger le CV (PDF)", f, file_name=f"CV_{clean_name}.pdf",
                               mime="application/pdf", use_container_width=True)

    # ---------- Cover Letter Generation ----------
    if gen_cl:
        cl_status = st.status("✍️ Génération de la lettre...")
        jd = st.session_state.get("jd_text", "")
        p = profile.get("personal_info", {})
        cl_name = st.session_state.get("edit_name", p.get("full_name", ""))
        cl_title = profile.get("personal_info", {}).get("title", "") or opt_data.get("title", "")
        cl_summary = opt_data.get("summary", profile.get("summary", ""))
        cl_skills = opt_data.get("skills", profile.get("skills", {}))
        cl_experience = opt_data.get("experience", profile.get("experience", []))
        cl_sk_list = []
        for cat, items in cl_skills.items():
            if items:
                for it in items:
                    cl_sk_list.append(it)
        cl_lang = st.session_state.get("cv_lang", "Français")

        cl_prompt = f"""Rédige une lettre de motivation professionnelle ET personnalisée pour l'offre ci-dessous.

Utilise ces informations du candidat :
- Nom : {cl_name}
- Titre : {cl_title}
- Résumé : {cl_summary}
- Compétences : {', '.join(cl_sk_list[:6])}
- Expériences : {', '.join([e.get('position','')+' chez '+e.get('company','') for e in cl_experience[:2]])}

Structure :
1. Coordonnées de l'expéditeur (en haut à droite)
2. Objet : Candidature pour [poste]
3. Corps (3 paragraphes max) :
   - Paragraphe 1 : Poste visé et motivation
   - Paragraphe 2 : Compétences clés et réalisations chiffrées en lien avec l'offre
   - Paragraphe 3 : Disponibilité et formule de politesse
4. Formule de politesse

Langue : {cl_lang}
RÈGLE : écris UNIQUEMENT dans cette langue. Pas de Baoulé.

Offre d'emploi :
{jd}"""

        cl_result = call_ai(cl_prompt, "Tu rédiges des lettres de motivation professionnelles, concises et percutantes.")
        cl_status.update(label="✅ Lettre générée !", state="complete", expanded=False)
        st.session_state.cover_letter = cl_result
        st.rerun()

    # Show cover letter if exists
    if st.session_state.cover_letter:
        st.divider()
        st.subheader("📝 Lettre de motivation")
        with st.expander("📋 Aperçu de la lettre", expanded=True):
            st.markdown(st.session_state.cover_letter)
        st.text_area("📄 Copier le texte", st.session_state.cover_letter, height=300, key="cl_text")


