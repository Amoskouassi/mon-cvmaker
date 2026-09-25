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

# Read API key from .env or Streamlit secrets
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()

if not os.environ.get("OPENROUTER_API_KEY"):
    try:
        os.environ["OPENROUTER_API_KEY"] = st.secrets["OPENROUTER_API_KEY"]
    except Exception:
        pass

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
if "add_cv_key" not in st.session_state:
    st.session_state.add_cv_key = 0
if "sb_user" not in st.session_state:
    st.session_state.sb_user = None
if "sb_client" not in st.session_state:
    st.session_state.sb_client = None
if "history" not in st.session_state:
    st.session_state.history = []
if "cache_key" not in st.session_state:
    st.session_state.cache_key = None

# ---------- Cloud sync (Supabase Auth) ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "") or st.secrets.get("supabase", {}).get("url", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or st.secrets.get("supabase", {}).get("anon_key", "")
SB_OK = bool(SUPABASE_URL and SUPABASE_KEY)

if SB_OK and st.session_state.sb_client is None:
    from supabase import create_client
    st.session_state.sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Restore auth session from URL on EVERY rerun (keeps session alive)
sb = st.session_state.sb_client if SB_OK else None
tok_b64 = st.query_params.get("sb_token")
if sb and tok_b64:
    try:
        tok = json.loads(base64.b64decode(tok_b64).decode("utf-8"))
        sb.auth.set_session(tok["a"], tok["r"])
        r = sb.auth.get_user()
        if r and r.user:
            st.session_state.sb_user = r.user
    except Exception:
        st.session_state.sb_user = None
elif sb:
    st.session_state.sb_user = None

def load_cloud():
    if not sb or not st.session_state.sb_user:
        return
    try:
        resp = sb.table("cv_texts").select("texts").eq("user_id", st.session_state.sb_user.id).execute()
        if resp.data:
            st.session_state.source_texts = resp.data[0].get("texts", [])
    except Exception as e:
        st.error(f"❌ Erreur chargement cloud : {e}")

def save_cloud():
    if not sb or not st.session_state.sb_user:
        return
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sb.table("cv_texts").upsert(
            {"user_id": st.session_state.sb_user.id, "texts": st.session_state.source_texts, "updated_at": now},
            on_conflict="user_id"
        ).execute()
    except Exception as e:
        st.error(f"❌ Erreur sauvegarde cloud : {e}")

# Auto-sync from cloud on every rerun
if sb and st.session_state.sb_user:
    try:
        resp = sb.table("cv_texts").select("texts,updated_at").eq("user_id", st.session_state.sb_user.id).execute()
        if resp.data:
            cloud = resp.data[0]
            cloud_texts = cloud.get("texts", [])
            cloud_ts = cloud.get("updated_at", "")
            if cloud_ts != st.session_state.get("last_sync_ts", ""):
                st.session_state.source_texts = cloud_texts
                st.session_state.last_sync_ts = cloud_ts
    except Exception as e:
        st.error(f"❌ Erreur synchro cloud : {e}")

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

            # Add CVs
            add_key = st.session_state.get("add_cv_key", 0)
            extra_pdfs = st.file_uploader("Ajouter des CV (PDF)", type=["pdf"], accept_multiple_files=True, key=f"add_cv_{add_key}")
            if extra_pdfs:
                added = 0
                for f in extra_pdfs:
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    text = "".join(page.get_text() for page in doc)
                    doc.close()
                    if text.strip():
                        st.session_state.source_texts.append(f"--- {f.name} ---\n{text.strip()}")
                        added += 1
                if added:
                    save_cloud()
                    st.session_state.add_cv_key = add_key + 1
                    st.rerun()

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

            # ---------- Historique ----------
            st.divider()
            st.subheader("📜 Historique")
            history = st.session_state.get("history", [])
            if history:
                for i, h in enumerate(history[-5:]):  # show last 5
                    cols = st.columns([3, 1])
                    with cols[0]:
                        st.caption(f"📄 {h.get('name', '?')} — {h.get('poste', '?')}")
                    with cols[1]:
                        if st.button("📂", key=f"hist_{i}", help="Charger"):
                            st.session_state.profile = h.get("profile")
                            st.session_state.opt_data = h.get("profile")
                            st.session_state.jd_text = h.get("jd", "")
                            st.rerun()
            else:
                st.caption("Aucun CV généré pour l'instant.")
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
    models = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "deepseek/deepseek-v4-flash-0731:free",
        "openrouter/free",
    ]
    for model in models:
        for attempt in range(3):
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
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
                    st.warning(f"⏳ {model} — tentative {attempt+1}/3 : {err_msg[:80]}. Attente {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices")
                if not choices or not choices[0].get("message", {}).get("content"):
                    err = data.get("error", {}).get("message", str(data)[:150])
                    st.warning(f"⏳ {model} — tentative {attempt+1}/3 : {err}")
                    time.sleep(10)
                    continue
                return choices[0]["message"]["content"].strip()
            except Exception as e:
                st.warning(f"⚠️ {model} — tentative {attempt+1}/3 : {e}")
                if attempt == 2:
                    break  # try next model
                time.sleep(10)
        st.warning(f"🔄 {model} indisponible, essai du modèle suivant...")
    st.error("❌ Tous les modèles sont indisponibles. Réessaie dans quelques minutes.")
    return ""
    return ""


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

    MAX_CHARS = 25000
    combined = "\n\n".join(all_texts)
    if len(combined) > MAX_CHARS:
        # Trop de CV pour la limite API → échantillonner la moitié (répartie sur toute la liste)
        n = max(1, len(all_texts) // 2)
        step = len(all_texts) / n
        sampled = []
        for i in range(n):
            t = all_texts[int(i * step)]
            if t not in sampled:
                sampled.append(t)
        seps = 2 * (len(sampled) - 1)
        per_cv = max(500, (MAX_CHARS - seps) // len(sampled))
        pieces = [t[:per_cv] for t in sampled]
        # Redistribuer le budget non utilisé sur les CV tronqués
        used = sum(len(p) for p in pieces) + seps
        leftover = MAX_CHARS - used
        if leftover > 0:
            for i, t in enumerate(sampled):
                if leftover <= 0:
                    break
                if len(pieces[i]) < len(t):
                    extra = min(leftover, len(t) - len(pieces[i]))
                    pieces[i] += t[len(pieces[i]):len(pieces[i]) + extra]
                    leftover -= extra
        combined = "\n\n".join(pieces)
        status.write(f"✂️ {len(all_texts)} CV détectés → échantillon de la moitié ({len(sampled)} CV) pour la limite de l'API.")
    if len(combined) > MAX_CHARS:
        combined = combined[:MAX_CHARS] + "\n\n[... suite tronquée pour la limite de l'API]"

    # --- Cache check ---
    import hashlib
    cache_key = hashlib.md5((combined + jd_text + cv_lang).encode()).hexdigest()
    if st.session_state.get("cache_key") == cache_key and st.session_state.get("profile"):
        st.info("♻️ Cache : même offre + mêmes CV → profil déjà généré.")
        st.rerun()

    status.write("⏳ **Fusion + optimisation IA...**")

    lang_map = {"Français": "FRANÇAIS", "English": "ENGLISH", "Español": "ESPAÑOL", "Português": "PORTUGUÊS"}
    lang_rule = lang_map.get(cv_lang, "FRANÇAIS")

    full_prompt = f"""Tu reçois plusieurs CV d'une même personne ET une offre d'emploi.
Ta mission : créer un CV SUR-MESURE pour cette offre précise. Tu ne gardes QUE ce qui est pertinent pour cette offre.

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
  "languages": [{{ "lang": "", "level": "" }}],
  "_target_company": "Nom de l'entreprise qui recrute (extrait de l'offre)"
}}}}

⚠️ RÈGLE D'OR : FILTRAGE AGRESSIF
Avant d'écrire le JSON, analyse l'offre d'emploi et identifie :
- Les compétences TECHNIQUES demandées
- Le domaine d'activité
- Le type de poste

Ensuite, pour CHAQUE élément des CV source, pose-toi la question : "Est-ce que ceci aide à obtenir CE poste précis ?"
- SI OUI → garde-le et optimise-le pour l'offre
- SI NON → SUPPRIME-LE complètement

Exemples de SUPPRESSION :
- Tu postules en dev web → SUPPRIME les expériences de cuisine, vente, manutention
- Tu postules en comptabilité → SUPPRIME les compétences de design graphique
- Le poste ne mentionne pas l'anglais → SUPPRIME les langues sauf le français
- Une formation sans rapport avec le poste → SUPPRIME-la

RÈGLES STRICTES :
1. personal_info : copie EXACTEMENT le nom, email, téléphone et localisation depuis les CV source. Ne modifie JAMAIS ces champs.
2. Compétences (skills) : MAX 8 compétences, UNIQUEMENT en lien DIRECT avec l'offre. Supprime tout le reste.
3. Outils (tools) : Dans "skills", utilise une catégorie séparée "Outils" pour les logiciels et outils (Kobo, ODK, SurveyCTO, Excel, Python, QGIS, etc.). NE LES MÉLANGE PAS avec les compétences générales.
4. Expériences : garde UNIQUEMENT les 2-3 expériences les plus pertinentes. SUPPRIME les autres. ORDONNE-LES du plus récent au plus ancien (end_date décroissant).
5. Education : garde UNIQUEMENT les diplômes en rapport avec le domaine. ORDONNE-LES du plus récent au plus ancien.
6. Certifications : garde UNIQUEMENT celles pertinentes pour l'offre.
7. Languages : garde UNIQUEMENT les langues mentionnées dans l'offre (ou le français par défaut).
8. Le "title" doit être IDENTIQUE ou très proche de l'intitulé du poste dans l'offre.
9. Le summary doit parler UNIQUEMENT de ce qui est pertinent pour cette offre.
10. Chaque achievement doit être un CHIFFRE des CV source, adapté au contexte de l'offre.
11. Langue du CV : {lang_rule} uniquement.
12. Format dates : "Janvier 2020 - Décembre 2022" (mois en français). Les dates doivent être cohérentes et triables.
13. _target_company : extrais le nom de l'entreprise depuis l'offre.
14. ATS : Utilise des mots-clés de l'offre dans le summary et les compétences. Structure claire et lisible par un robot de recrutement.
15. Réponds STRICTEMENT en JSON, sans texte avant ni après.

⚠️ ANTI-HALLUCINATION (RÈGLE ABSOLUE) :
- Copie le nom, email, téléphone EXACTEMENT comme dans les CV source.
- Si un champ est vide dans les CV source, laisse-le vide.
- N'invente JAMAIS de chiffres, résultats, entreprises, postes ou compétences.
- Ne crée PAS de nouvelles expériences. Utilise UNIQUEMENT celles des CV source.
- En cas de doute → SUPPRIME au lieu d'ajouter.

CVs à fusionner :
{combined}

Offre à cibler :
{jd_text}"""

    with st.spinner("🤖 Fusion IA en cours... (1-2 min)"):
        result_json = call_ai(full_prompt, "Tu es un assistant spécialisé en CV. Réponds UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ni après. Pas de ```json ni ```.")
    if not result_json:
        st.error("❌ Échec de l'appel IA. Réessaie dans quelques secondes.")
        st.stop()
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
    if not isinstance(profile, dict):
        st.error("❌ L'IA n'a pas retourné un objet CV valide.")
        st.code(str(profile)[:1500])
        st.info("💡 Relance la génération.")
        st.stop()

    # --- Vérification post-génération ---
    status.write("🔍 **Vérification pertinence + anti-hallucination...**")
    verify_prompt = f"""Voici un CV généré par IA pour une offre d'emploi. Vérifie DEUX choses :

1. PERTINENCE : Est-ce que chaque élément du CV a un rapport avec l'offre ? Si non → supprime-le.
2. ANTI-HALLUCINATION : Est-ce que chaque info existe dans les CV source ? Si non → corrige-le.

CV généré :
{json.dumps(profile, ensure_ascii=False, indent=2)}

CV source :
{combined}

Offre d'emploi :
{jd_text}

Retourne UNIQUEMENT un JSON avec cette structure :
{{"corrections": {{ "champ_corrigé": "valeur_corrigée" }}, "issues": ["description du problème trouvé"]}}

RÈGLES :
- Si le nom, email ou téléphone ne correspond PAS aux CV source → corrige-le
- Si une expérience, compétence ou formation n'est PAS dans les CV source → supprime-la
- Si un élément n'a AUCUN rapport avec l'offre → supprime-le (liste-le dans "issues")
- Si un chiffre ou résultat n'est PAS dans les CV source → supprime-le
- Si une date est incohérente ou mal formatée → corrige-la
- Si les expériences ne sont pas du plus récent au plus ancien → réordonne-les
- Si un outil (Kobo, ODK, etc.) est dans les compétences au lieu des outils → déplace-le
- Si tout est correct, retourne {{"corrections": {{}}, "issues": []}}
- Réponds UNIQUEMENT avec le JSON, sans texte avant ni après."""

    verify_result = call_ai(verify_prompt, "Tu es un vérificateur de CV strict et qualitatif. Tu détectes les pertinences, hallucinations et erreurs de format.")
    if verify_result:
        verify_result = re.sub(r"```(?:json)?\s*", "", verify_result).strip()
        v_match = re.search(r"\{.*\}", verify_result, re.DOTALL)
        if v_match:
            try:
                verification = json.loads(v_match.group())
                if not isinstance(verification, dict):
                    raise ValueError("verification not a dict")
                corrections = verification.get("corrections", {})
                issues = verification.get("issues", [])
                if not isinstance(corrections, dict):
                    corrections = {}
                if not isinstance(issues, list):
                    issues = [str(issues)]
                if corrections:
                    for k, v in corrections.items():
                        try:
                            if "." in k:
                                parts = k.split(".")
                                obj = profile
                                for p in parts[:-1]:
                                    if isinstance(obj, dict):
                                        obj = obj.get(p, {})
                                    elif isinstance(obj, list) and p.isdigit():
                                        idx = int(p)
                                        obj = obj[idx] if idx < len(obj) else None
                                    else:
                                        obj = None
                                        break
                                if isinstance(obj, dict):
                                    obj[parts[-1]] = v
                            else:
                                profile[k] = v
                        except (AttributeError, IndexError, TypeError):
                            pass
                    st.warning(f"🔧 {len(corrections)} correction(s) appliquée(s) : {', '.join(str(k) for k in corrections.keys())}")
                if issues:
                    st.info(f"⚠️ {len(issues)} problème(s) détecté(s) : {'; '.join(str(i) for i in issues[:3])}")
            except Exception:
                pass

    # Normalisation de sécurité du profil
    if not isinstance(profile, dict):
        st.error("❌ Profil invalide après vérification.")
        st.stop()
    if not isinstance(profile.get("personal_info"), dict):
        profile["personal_info"] = {}
    for kf in ("summary", "title"):
        if not isinstance(profile.get(kf, ""), str):
            profile[kf] = str(profile.get(kf, ""))

    status.update(label="✅ **CV fusionné et optimisé !**", state="complete", expanded=False)

    save_cloud()

    st.session_state.profile = profile
    st.session_state.opt_data = profile
    st.session_state.extracted = True
    st.session_state.cv_lang = cv_lang
    st.session_state.jd_text = jd_text
    st.session_state.cache_key = cache_key

    # Save to history
    hist = st.session_state.get("history", [])
    p_info = profile.get("personal_info", {})
    hist.append({
        "name": p_info.get("full_name", "?"),
        "poste": p_info.get("title", "?"),
        "jd": jd_text[:200],
        "profile": profile,
    })
    st.session_state.history = hist[-10:]  # keep last 10
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
        sk = profile.get("skills", {})
        if isinstance(sk, dict):
            for cat, items in sk.items():
                if items:
                    st.markdown(f"**{cat} :** {', '.join(str(i) for i in items)}")

    # --- Score ATS ---
    jd = st.session_state.get("jd_text", "")
    if jd:
        # Extract keywords from JD (simple approach)
        jd_lower = jd.lower()
        cv_text = json.dumps(profile, ensure_ascii=False).lower()
        # Common ATS keywords (skills, tools, languages)
        import re as re_mod
        keywords = set()
        # Extract words > 3 chars from JD
        for word in re_mod.findall(r'\b[a-zA-Zàâäéèêëîïôöùûüÿç]{4,}\b', jd_lower):
            if word not in ('dans', 'pour', 'avec', 'vous', 'nous', 'sont', 'être', 'avoir', 'cette', 'ceci', 'ainsi', 'mais', 'donc', 'plus', 'très', 'tout', 'tous', 'toute', 'toutes', 'leur', 'leurs', 'sous', 'sur', 'des', 'les', 'une', 'aux', 'par', 'que', 'qui', 'est', 'ont', 'ses', 'son', 'sa ', ' de '):
                keywords.add(word)
        # Check which keywords are in CV
        found = [k for k in keywords if k in cv_text]
        missing = [k for k in keywords if k not in cv_text]
        total = len(keywords) or 1
        score = int(len(found) / total * 100)

        with st.expander(f"🎯 Score ATS : {score}%", expanded=False):
            st.progress(score / 100)
            if score >= 70:
                st.success(f"✅ Excellent ! {len(found)}/{total} mots-clés présents.")
            elif score >= 40:
                st.warning(f"⚠️ Moyen. {len(found)}/{total} mots-clés présents.")
            else:
                st.error(f"❌ Faible. {len(found)}/{total} mots-clés présents.")
            if missing:
                st.markdown("**Mots-clés manquants (à ajouter si pertinent) :**")
                st.code(", ".join(sorted(missing)[:20]))

    # Let user edit personal info before PDF generation
    st.subheader("✏️ Vérifie et modifie tes informations")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Nom complet", value=p.get("full_name", p.get("name", "Candidat")), key="edit_name")
        email = st.text_input("Email", value=p.get("email", ""), key="edit_email")
        poste = st.text_input("Poste visé", value=p.get("title", ""), key="edit_poste")
    with col2:
        phone = st.text_input("Téléphone", value=p.get("phone", ""), key="edit_phone")
        location = st.text_input("Localisation", value=p.get("location", ""), key="edit_location")
        company = st.text_input("Entreprise ciblée", value=profile.get("_target_company", ""), key="edit_company")

    if st.button("💾 Sauvegarder les modifications manuelles", use_container_width=True, key="btn_save_manual"):
        profile.setdefault("personal_info", {})["full_name"] = name
        profile.setdefault("personal_info", {})["email"] = email
        profile.setdefault("personal_info", {})["title"] = poste
        profile.setdefault("personal_info", {})["phone"] = phone
        profile.setdefault("personal_info", {})["location"] = location
        profile["_target_company"] = company
        st.session_state.profile = profile
        st.session_state.opt_data = profile
        save_cloud()
        st.success("✅ Modifications enregistrées !")
        st.rerun()

    st.divider()
    st.subheader("🤖 Modification par IA")
    mod_prompt = st.text_area("Dis à l'IA ce que tu veux modifier",
                              placeholder='Ex: "ajoute Python à mes compétences", "change le titre en Senior Developer", "supprime l\'expérience chez X"',
                              key="mod_prompt")
    if st.button("✏️ Appliquer la modification", use_container_width=True, key="btn_mod") and mod_prompt.strip():
        with st.spinner("🤖 Modification en cours..."):
            current_json = json.dumps(st.session_state.opt_data or st.session_state.profile, ensure_ascii=False, indent=2)
            mod_result = call_ai(
                f"Voici un profil CV en JSON. Applique UNIQUEMENT la modification demandée sans changer le reste.\n\n"
                f"Profil actuel :\n{current_json}\n\n"
                f"Modification demandée : {mod_prompt}\n\n"
                f"Réponds UNIQUEMENT avec le JSON complet modifié, sans texte avant ni après.",
                "Tu modifies un profil CV. Retourne UNIQUEMENT le JSON modifié, valide, sans aucun texte autour."
            )
            if not mod_result:
                st.error("❌ Échec de l'appel IA pour la modification.")
            else:
                mod_result = re.sub(r"```(?:json)?\s*", "", mod_result).strip()
                json_match = re.search(r"\{.*\}", mod_result, re.DOTALL)
                if json_match:
                    mod_result = json_match.group()
                try:
                    new_profile = json.loads(mod_result)
                    old_name = st.session_state.profile.get("personal_info", {}).get("full_name", "")
                    new_name = new_profile.get("personal_info", {}).get("full_name", "")
                    st.session_state.profile = new_profile
                    st.session_state.opt_data = new_profile
                    for k in ["edit_name","edit_email","edit_poste","edit_phone","edit_location","edit_company"]:
                        if k in st.session_state:
                            del st.session_state[k]
                    if old_name and old_name != new_name:
                        save_cloud()
                    st.success("✅ CV modifié avec succès !")
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("❌ L'IA n'a pas retourné un JSON valide. Réponse brute :")
                    st.code(mod_result[:1500])

    gen_pdf = st.button("✅ Générer le PDF", use_container_width=True, type="primary", key="gen_pdf")
    gen_cl = st.button("✍️ Générer la lettre de motivation", use_container_width=True, key="gen_cl")

    # ---------- PDF Generation ----------
    if gen_pdf:
        status = st.status("📝 Génération du PDF...")
        import tempfile, os, subprocess, sys, base64
        from pathlib import Path

        summary = opt_data.get("summary", profile.get("summary", ""))
        skills = opt_data.get("skills", profile.get("skills", {}))
        if not isinstance(skills, dict):
            skills = {}

        def date_sort_key(x):
            """Extract YYYYMM from date string for proper sorting."""
            d = x.get("end_date", "") or x.get("start_date", "") or ""
            months = {"janvier":"01","février":"02","fevrier":"02","mars":"03","avril":"04","mai":"05",
                      "juin":"06","juillet":"07","août":"08","aout":"08","septembre":"09","octobre":"10",
                      "novembre":"11","décembre":"12","decembre":"12",
                      "january":"01","february":"02","march":"03","april":"04","may":"05","june":"06",
                      "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12",
                      "jan":"01","feb":"02","mar":"03","apr":"04","jun":"06","jul":"07","aug":"08",
                      "sep":"09","oct":"10","nov":"11","dec":"12"}
            d_lower = d.lower().strip()
            year = ""
            month = "00"
            for y in ["2027","2026","2025","2024","2023","2022","2021","2020","2019","2018","2017","2016","2015"]:
                if y in d:
                    year = y
                    break
            for m_name, m_num in months.items():
                if m_name in d_lower:
                    month = m_num
                    break
            if not year:
                # Try to extract any 4-digit year
                import re as _re
                yr = _re.search(r"(\d{4})", d)
                if yr:
                    year = yr.group(1)
            return year + month

        def as_list(v):
            return v if isinstance(v, list) else []

        def as_dicts(v):
            return [x for x in as_list(v) if isinstance(x, dict)]

        experience = sorted(as_dicts(opt_data.get("experience", profile.get("experience", []))),
                            key=date_sort_key, reverse=True)
        education = sorted(as_dicts(profile.get("education", [])),
                           key=date_sort_key, reverse=True)
        languages = profile.get("languages", [])
        if not isinstance(languages, list):
            languages = []

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
        tool_cats = {"Outils", "Tools", "Technologies", "Logiciels", "Outils Numériques", "Logiciel", "Outil", "Tool", "Tools & Technologies"}
        tool_names = {"kobo", "odk", "surveycto", "qlik", "powerbi", "power bi", "tableau", "excel", "word", "access",
                      "python", "r", "stata", "spss", "sql", "postgis", "qgis", "arcgis", "gis",
                      "ms office", "suite ms office", "google suite", "googlesheets", "google sheets",
                      "kobo toolbox", "enumerate", "cspro", "moodle", "sakai", "canva", "figma",
                      "syntax", "atlasti", "nvivo", "dedoose", "commcare", "sap", "erp", "crm",
                      "sage", "quickbooks", "tally", "solarwinds", "wireshark", "cisco"}
        lang_list = [f"<li>{esc_html(l.get('lang',''))} : {esc_html(l.get('level',''))}</li>" for l in languages]
        tool_list = []
        skill_list = []
        for cat, items in skills.items():
            if items:
                if cat in tool_cats:
                    tool_list.extend(f"<li>{esc_html(it)}</li>" for it in items)
                else:
                    for it in items:
                        if it.lower().strip() in tool_names:
                            tool_list.append(f"<li>{esc_html(it)}</li>")
                        else:
                            skill_list.append(f"<li>{esc_html(it)}</li>")
        if not lang_list: lang_list = ["<li>Fran\u00e7ais : Natif</li>"]
        if not tool_list: tool_list = ["<li>Suite MS Office</li>"]
        if not skill_list: skill_list = ["<li>P\u00e9dagogie</li>"]
        skill_list = skill_list[:8]
        tool_list = tool_list[:8]

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

        status.update(label="✅ CV optimisé généré !", state="complete", expanded=False)

        # Build filename: Nom_Prenom_Poste_Entreprise_CV.pdf
        clean_name = re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")
        clean_poste = re.sub(r'[\\/*?:"<>|]', "", poste).replace(" ", "_") if poste else ""
        clean_company = re.sub(r'[\\/*?:"<>|]', "", company).replace(" ", "_") if company else ""
        parts = [p for p in [clean_name, clean_poste, clean_company, "CV"] if p]
        pdf_filename = "_".join(parts) + ".pdf"

        with open(out_file, "rb") as f:
            pdf_bytes = f.read()
        pdf_b64 = base64.b64encode(pdf_bytes).decode()
        pdf_data_uri = f"data:application/pdf;base64,{pdf_b64}"

        st.markdown(
            f'<div style="text-align:center;margin-bottom:12px;">'
            f'<a href="{pdf_data_uri}" target="_blank" '
            f'style="display:inline-block;padding:12px 24px;background:#C5A059;'
            f'color:white;text-decoration:none;border-radius:6px;font-weight:700;">'
            f'👁️ Ouvrir le PDF dans un nouvel onglet</a></div>',
            unsafe_allow_html=True
        )

        st.download_button("📄 Télécharger le CV (PDF)", data=pdf_bytes, file_name=pdf_filename,
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
        if not isinstance(cl_skills, dict):
            cl_skills = {}
        cl_experience = opt_data.get("experience", profile.get("experience", []))
        if not isinstance(cl_experience, list):
            cl_experience = []
        cl_experience = [e for e in cl_experience if isinstance(e, dict)]
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

⚠️ ANTI-HALLUCINATION : N'invente JAMAIS de compétences, expériences ou résultats. Utilise UNIQUEMENT les informations fournies ci-dessus. Si tu n'as pas assez de matière, raccourcis plutôt qu'inventer.

Offre d'emploi :
{jd}"""

        cl_result = call_ai(cl_prompt, "Tu rédiges des lettres de motivation professionnelles, concises et percutantes.")
        if cl_result:
            cl_status.update(label="✅ Lettre générée !", state="complete", expanded=False)
            st.session_state.cover_letter = cl_result
            st.rerun()
        else:
            cl_status.update(label="❌ Échec de la génération", state="error", expanded=False)

    # Show cover letter if exists
    if st.session_state.cover_letter:
        st.divider()
        st.subheader("📝 Lettre de motivation")
        with st.expander("📋 Aperçu de la lettre", expanded=True):
            st.markdown(st.session_state.cover_letter)
        cl_text = st.session_state.cover_letter
        st.text_area("📄 Copier le texte", cl_text, height=300, key="cl_text")

        # Build LM filename
        cl_name = st.session_state.get("edit_name", "").replace(" ", "_")
        cl_poste = st.session_state.get("edit_poste", "").replace(" ", "_") if st.session_state.get("edit_poste") else ""
        cl_company = st.session_state.get("edit_company", "").replace(" ", "_") if st.session_state.get("edit_company") else ""
        cl_parts = [p for p in [cl_name, cl_poste, cl_company, "LM"] if p]
        cl_filename = "_".join(cl_parts) + ".txt"
        st.download_button("📥 Télécharger la lettre (.txt)", data=cl_text.encode("utf-8"),
                           file_name=cl_filename, use_container_width=True)

        # PDF export for cover letter
        if st.button("📄 Générer la lettre en PDF", use_container_width=True, key="gen_cl_pdf"):
            with st.spinner("📄 Génération du PDF..."):
                import tempfile, os, subprocess, sys, base64 as b64mod
                from pathlib import Path as P

                def cl_esc(t):
                    if not t: return ""
                    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

                # Convert markdown-ish to HTML paragraphs
                cl_html_body = ""
                for line in cl_text.split("\n"):
                    line = line.strip()
                    if line:
                        cl_html_body += f"<p>{cl_esc(line)}</p>\n"
                    else:
                        cl_html_body += "<br/>\n"

                cl_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap" rel="stylesheet"/>
<style>
@page {{ size: A4; margin: 20mm; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Montserrat', sans-serif; font-size: 12px; color: #333; line-height: 1.6; }}
p {{ margin-bottom: 10px; }}
</style></head><body>{cl_html_body}</body></html>"""

                out_file = P(tempfile.mktemp(suffix=".pdf"))
                is_linux = sys.platform.startswith("linux")
                if is_linux:
                    from weasyprint import HTML
                    HTML(string=cl_html).write_pdf(out_file)
                else:
                    edge_candidates = [
                        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                    ]
                    edge = next((e for e in edge_candidates if os.path.exists(e)), None)
                    if edge:
                        html_path = P(tempfile.mktemp(suffix=".html"))
                        html_path.write_text(cl_html, encoding="utf-8")
                        subprocess.run(
                            [edge, "--headless", f"--print-to-pdf={out_file}", "--disable-gpu",
                             "--no-first-run", f"file:///{html_path.as_posix()}"],
                            capture_output=True, timeout=60,
                        )

                if out_file.exists():
                    with open(out_file, "rb") as f:
                        cl_pdf_bytes = f.read()
                    cl_pdf_filename = "_".join(cl_parts) + ".pdf"
                    st.download_button("📄 Télécharger la lettre (PDF)", data=cl_pdf_bytes,
                                       file_name=cl_pdf_filename, mime="application/pdf",
                                       use_container_width=True, key="dl_cl_pdf")
                else:
                    st.error("❌ Erreur génération PDF LM.")


