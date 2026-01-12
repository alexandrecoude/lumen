import re
import json
import time
import pandas as pd
import streamlit as st
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

st.set_page_config(page_title="LLM Product Page Auditor", layout="wide")
st.title("LLM Product Page Auditor — MVP")

SITEMAP_RE = re.compile(r"<loc>(.*?)</loc>", re.IGNORECASE)

def normalize_root(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")

def same_domain(url: str, root: str) -> bool:
    return urlparse(url).netloc == urlparse(root).netloc

def url_allowed(url: str, include_patterns, exclude_patterns) -> bool:
    if any(re.search(p, url) for p in exclude_patterns if p):
        return False
    if include_patterns and not any(re.search(p, url) for p in include_patterns if p):
        return False
    return True

def fetch_text(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text

def discover_urls(root_url: str, user_agent: str) -> list[str]:
    sitemap_url = urljoin(root_url.rstrip("/") + "/", "sitemap.xml")
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        try:
            xml = fetch_text(client, sitemap_url)
            locs = SITEMAP_RE.findall(xml)

            urls: list[str] = []
            for loc in locs:
                if loc.endswith(".xml") and "sitemap" in loc:
                    try:
                        subxml = fetch_text(client, loc)
                        urls.extend(SITEMAP_RE.findall(subxml))
                    except Exception:
                        pass
                else:
                    urls.append(loc)

            urls = sorted(set(urls))
            if urls:
                return urls
        except Exception:
            pass

    # fallback minimal
    return [root_url]

def extract_jsonld(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text(strip=True))
            if isinstance(data, list):
                out.extend([d for d in data if isinstance(d, dict)])
            elif isinstance(data, dict):
                out.append(data)
        except Exception:
            continue
    return out

def has_product_schema(jsonlds: list[dict]) -> bool:
    for d in jsonlds:
        t = d.get("@type")
        if t == "Product" or (isinstance(t, list) and "Product" in t):
            return True
        # parfois dans @graph
        if "@graph" in d and isinstance(d["@graph"], list):
            for g in d["@graph"]:
                if isinstance(g, dict):
                    gt = g.get("@type")
                    if gt == "Product" or (isinstance(gt, list) and "Product" in gt):
                        return True
    return False

def score_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()

    jsonlds = extract_jsonld(html)
    product_schema = has_product_schema(jsonlds)

    findings = {
        "has_jsonld": len(jsonlds) > 0,
        "has_product_schema": product_schema,
        "has_specs_table": bool(soup.select("table")),
        "mentions_reviews": any(k in text for k in ["avis", "reviews", "étoiles", "etoiles", "note"]),
        "has_faq": any(k in text for k in ["faq", "questions fréquentes", "questions frequentes"]),
        "has_many_images": len(soup.select("img")) >= 3,
    }

    weights = {
        "has_product_schema": 40,
        "has_jsonld": 10,
        "has_specs_table": 15,
        "mentions_reviews": 15,
        "has_faq": 10,
        "has_many_images": 10,
    }
    score = sum(weights[k] for k in weights if findings.get(k))

    recos = []
    if not findings["has_product_schema"]:
        recos.append("Ajouter JSON-LD schema.org Product + Offer (prix, devise, stock, GTIN/SKU).")
    if not findings["has_specs_table"]:
        recos.append("Ajouter un tableau de specs (matière, poids, dimensions, normes…).")
    if not findings["has_faq"]:
        recos.append("Ajouter une FAQ produit (8–15 Q/R) + balisage FAQPage.")
    if not findings["mentions_reviews"]:
        recos.append("Ajouter des avis clients + AggregateRating.")
    if not findings["has_many_images"]:
        recos.append("Ajouter plus d’images + alt descriptifs.")

    return {"score": score, "findings": findings, "recommendations": recos}

def scan_site(root_url: str, max_pages: int, include_patterns, exclude_patterns, rps: float, user_agent: str):
    urls = discover_urls(root_url, user_agent)
    urls = [u for u in urls if same_domain(u, root_url)]
    urls = [u for u in urls if url_allowed(u, include_patterns, exclude_patterns)]
    urls = urls[:max_pages]

    results = []
    delay = 1.0 / max(rps, 0.1)

    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        for u in urls:
            try:
                r = client.get(u)
                ct = r.headers.get("content-type", "")
                html = r.text if "text/html" in ct else ""
                data = score_page(html) if html else {"score": 0, "findings": {}, "recommendations": ["Page non HTML ou inaccessible."]}
                page_type = "product" if data["findings"].get("has_product_schema") else "other"
                results.append({
                    "url": u,
                    "status": r.status_code,
                    "type": page_type,
                    "score": data["score"],
                    "recommendations": " | ".join(data["recommendations"][:4]),
                })
            except Exception:
                results.append({
                    "url": u,
                    "status": 0,
                    "type": "error",
                    "score": 0,
                    "recommendations": "Erreur de fetch",
                })
            time.sleep(delay)

    results.sort(key=lambda x: (0 if x["type"] == "product" else 1, x["score"]))
    return results


# -------- UI --------
with st.sidebar:
    st.header("Paramètres")
    root = st.text_input("URL du site", value="https://www.vetdepro.com")
    max_pages = st.slider("Nombre max de pages", 10, 300, 60)
    rps = st.slider("Vitesse (req/sec)", 0.5, 5.0, 2.0, 0.5)

    st.subheader("Filtres (regex)")
    preset = st.selectbox(
        "Preset",
        ["Aucun", "Pages produits (générique .html)", "Shopify (/products/)", "Prestashop (souvent .html)"],
        index=1
    )
    if preset == "Shopify (/products/)":
        include_default = "/products/"
    elif preset == "Prestashop (souvent .html)":
        include_default = ".*\\.html$"
    elif preset == "Pages produits (générique .html)":
        include_default = "/[0-9]+-.*\\.html$"
    else:
        include_default = ""

    include_pattern = st.text_input("Inclure (regex)", value=include_default)
    exclude_pattern = st.text_input("Exclure (regex)", value="\\?|#|/cart|/checkout|/account")

    user_agent = st.text_input("User-Agent", value="MVP-Auditor/1.0 (+contact@example.com)")
    run = st.button("Lancer le scan", type="primary")

if run:
    root_url = normalize_root(root)
    include_patterns = [include_pattern] if include_pattern else []
    exclude_patterns = exclude_pattern.split("|") if exclude_pattern else []

    st.info("Scan en cours…")
    results = scan_site(root_url, max_pages, include_patterns, exclude_patterns, rps, user_agent)

    df = pd.DataFrame(results)
    st.success(f"Scan terminé — {len(df)} pages analysées.")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Télécharger CSV", csv, file_name="audit_pages.csv", mime="text/csv")
else:
    st.write("➡️ Renseigne l’URL puis clique **Lancer le scan**.")
