import streamlit as st
import re
import json
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import time

st.set_page_config(
    page_title="LLM Product Page Auditor",
    page_icon="🔍",
    layout="wide"
)

# Configuration du style simple
st.markdown("""
<style>
    .main > div {
        padding-top: 2rem;
    }
    .stAlert {
        margin-top: 1rem;
    }
</style>
""", unsafe_allow_html=True)

st.title("🔍 LLM Product Page Auditor - Optimisation GEO")
st.markdown("**Auditez et optimisez vos pages produits pour les LLMs** (ChatGPT, Claude, Perplexity, etc.) : analyse approfondie, scoring par catégorie et recommandations priorisées.")

# Sidebar pour les paramètres
with st.sidebar:
    st.header("⚙️ Paramètres du scan")
    
    with st.expander("💡 C'est quoi le GEO ?", expanded=False):
        st.markdown("""
        **GEO = Generative Engine Optimization**
        
        C'est l'optimisation pour les LLMs comme :
        - 🤖 ChatGPT (OpenAI)
        - 🧠 Claude (Anthropic)  
        - 🔍 Perplexity
        - 💬 Gemini (Google)
        
        **Pourquoi c'est important ?**
        Les gens posent de plus en plus de questions aux IA plutôt qu'à Google. Pour que vos produits soient recommandés, il faut :
        
        ✅ Données structurées complètes
        ✅ Contenu riche et contextualisé
        ✅ Signaux d'autorité/crédibilité
        ✅ FAQ et réponses directes
        
        **Le GEO = le nouveau SEO !**
        """)
    
    st.info("""
    💡 **Astuce** : 
    - Pour scanner **tout un site** → utilisez l'URL racine + un template
    - Pour scanner **une page unique** → entrez l'URL complète de la page
    """)
    
    max_pages = st.number_input("Nombre max de pages", min_value=1, max_value=200, value=50)
    
    st.subheader("Filtres d'URL")
    st.caption("Les filtres permettent de cibler uniquement les pages produits")
    
    template = st.selectbox(
        "Template prédéfini",
        ["Personnalisé", "Shopify", "PrestaShop", "Magento", "WooCommerce", "Aucun filtre"]
    )
    
    if template == "Shopify":
        include_pattern = "/products/"
        exclude_patterns = ["/account", "/cart", "/checkout", "/collections"]
    elif template == "PrestaShop":
        include_pattern = r"/[0-9]+-.*\.html$"
        exclude_patterns = ["/panier", "/commande", "/mon-compte"]
    elif template == "Magento":
        include_pattern = r"\.html$"
        exclude_patterns = ["/checkout", "/customer", "/cart"]
    elif template == "WooCommerce":
        include_pattern = "/product/"
        exclude_patterns = ["/cart", "/checkout", "/my-account"]
    elif template == "Aucun filtre":
        include_pattern = ""
        exclude_patterns = []
        st.success("✅ Mode 'Aucun filtre' activé - Toutes les pages seront analysées")
        st.info(f"🔍 Inclusions : `(aucune)` | Exclusions : `(aucune)`")
    else:
        include_pattern = st.text_input(
            "Pattern d'inclusion (regex)",
            value="",
            help="Ex: /products/ ou /[0-9]+-.*\.html$ - Laissez vide pour tout inclure"
        )
        exclude_patterns_text = st.text_area(
            "Patterns d'exclusion (un par ligne)",
            value="/account\n/cart\n/checkout",
            help="URLs à exclure du scan - Laissez vide pour ne rien exclure"
        )
        exclude_patterns = [p.strip() for p in exclude_patterns_text.split("\n") if p.strip()]

# Regex pour parser le sitemap
SITEMAP_RE = re.compile(r"<loc>(.*?)</loc>", re.IGNORECASE)

def same_domain(url: str, root: str) -> bool:
    """Vérifie si l'URL appartient au même domaine que la racine (gère www/non-www)"""
    url_domain = urlparse(url).netloc.lower().replace('www.', '')
    root_domain = urlparse(root).netloc.lower().replace('www.', '')
    return url_domain == root_domain

def url_allowed(url: str, include_pattern: str, exclude_patterns: list) -> bool:
    """Vérifie si l'URL respecte les filtres"""
    # Exclusions
    if any(re.search(p, url) for p in exclude_patterns):
        return False
    # Inclusions (si pattern défini)
    if include_pattern and not re.search(include_pattern, url):
        return False
    return True

async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Télécharge le contenu texte d'une URL"""
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.text

def extract_categories_from_urls(urls: list[str], base_domain: str) -> dict:
    """
    Détecte les VRAIES pages catégories (URLs courtes) et les sous-catégories.
    
    Logique :
    - Niveau 1 : URLs avec 1-2 segments (pages catégories comme /5-pantalon-de-travail)
    - Niveau 2 : Extraction du segment catégorie dans les URLs produits
    """
    from collections import Counter
    
    category_pages = []  # URLs complètes des pages catégories
    level1_categories = []  # Noms des catégories (depuis URLs courtes)
    level2_categories = []  # Catégories extraites des URLs produits
    all_segments_debug = []
    
    # Segments techniques à exclure
    excluded = ['sitemap', 'wp-content', 'wp-json', 'admin', 'api', 
                'cart', 'checkout', 'account', 'login', 'register']
    
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        if not path:
            continue
            
        segments = [s for s in path.split('/') if s and s.lower() not in excluded]
        
        # Debug : garder trace
        if len(segments) > 0:
            all_segments_debug.append((url, segments, len(segments)))
        
        # NIVEAU 1 : Détecter les pages CATÉGORIES (URLs courtes, 1-2 segments max)
        if len(segments) == 1:
            # URL comme /5-pantalon-de-travail
            category_pages.append((url, segments[0]))
            level1_categories.append(segments[0])
        
        elif len(segments) == 2 and not segments[1].isdigit():
            # URL comme /categorie/sous-categorie (mais pas /categorie/123)
            category_pages.append((url, segments[0]))
            level1_categories.append(segments[0])
        
        # NIVEAU 2 : Extraire catégories depuis URLs PRODUITS (URLs longues, 3+ segments)
        elif len(segments) >= 3:
            # URL comme /content/5-pantalon-de-travail/produit-123
            # On veut extraire "5-pantalon-de-travail"
            
            # Trouver le segment qui ressemble à une catégorie (pas "content", pas un ID numérique)
            generic_prefixes = ['content', 'product', 'produit', 'item', 'article', 'shop', 'boutique']
            
            for i, seg in enumerate(segments):
                # Ignorer les préfixes génériques et les IDs numériques purs
                if seg.lower() not in generic_prefixes and not seg.isdigit() and len(seg) > 2:
                    # C'est probablement une catégorie
                    level2_categories.append(seg)
                    break
    
    # Compter les occurrences
    level1_counts = Counter(level1_categories)
    level2_counts = Counter(level2_categories)
    
    return {
        'level1': dict(sorted(level1_counts.items(), key=lambda x: x[1], reverse=True)),
        'level2': dict(sorted(level2_counts.items(), key=lambda x: x[1], reverse=True)),
        'category_pages': category_pages[:100],  # Garder les 100 premières pages catégories
        'all_segments': all_segments_debug[:50],
        'total_urls': len(urls),
        'short_urls': len([s for s in all_segments_debug if s[2] <= 2]),  # URLs courtes
        'long_urls': len([s for s in all_segments_debug if s[2] >= 3])  # URLs longues
    }

def filter_urls_by_categories(urls: list[str], selected_categories: list[str], level: str = 'level1') -> list[str]:
    """
    Filtre les URLs par catégories.
    
    Niveau 1 : Filtre les pages catégories (URLs courtes)
    Niveau 2 : Filtre les pages produits qui contiennent la catégorie
    """
    if not selected_categories or "Toutes les catégories" in selected_categories:
        return urls
    
    filtered = []
    excluded = ['sitemap', 'wp-content', 'wp-json', 'admin', 'api']
    
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        if not path:
            continue
            
        segments = [s for s in path.split('/') if s and s.lower() not in excluded]
        
        if level == 'level1':
            # Filtre sur les URLs courtes (pages catégories)
            if len(segments) <= 2 and len(segments) >= 1:
                if segments[0] in selected_categories:
                    filtered.append(url)
        
        elif level == 'level2':
            # Filtre sur les URLs longues (pages produits contenant la catégorie)
            if len(segments) >= 3:
                # Chercher la catégorie dans les segments
                generic_prefixes = ['content', 'product', 'produit', 'item', 'article', 'shop', 'boutique']
                for seg in segments:
                    if seg.lower() not in generic_prefixes and not seg.isdigit() and seg in selected_categories:
                        filtered.append(url)
                        break
    
    return filtered

async def discover_urls(root_url: str, progress_bar) -> list[str]:
    """Découvre les URLs via sitemap.xml"""
    sitemap_url = urljoin(root_url.rstrip("/") + "/", "sitemap.xml")
    
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "LLM-Product-Auditor/1.0"}
    ) as client:
        try:
            progress_bar.progress(0.1, text="🔍 Recherche du sitemap...")
            xml = await fetch_text(client, sitemap_url)
            locs = SITEMAP_RE.findall(xml)
            
            # Nettoyer les balises CDATA des URLs
            def clean_url(url):
                """Nettoie les balises XML comme <![CDATA[ et ]]>"""
                url = url.replace('<![CDATA[', '').replace(']]>', '').strip()
                return url
            
            locs = [clean_url(loc) for loc in locs]
            urls = []
            
            # Gestion des sitemap index (multiples sitemaps)
            sitemap_files = [loc for loc in locs if loc.endswith(".xml") and "sitemap" in loc.lower()]
            
            if sitemap_files:
                progress_bar.progress(0.2, text=f"📄 {len(sitemap_files)} sitemap(s) trouvé(s)...")
                for i, sitemap_file in enumerate(sitemap_files):
                    try:
                        subxml = await fetch_text(client, sitemap_file)
                        sub_locs = SITEMAP_RE.findall(subxml)
                        sub_locs = [clean_url(loc) for loc in sub_locs]
                        urls.extend(sub_locs)
                        progress_bar.progress(0.2 + (i+1)/len(sitemap_files) * 0.2, 
                                            text=f"📄 Lecture sitemap {i+1}/{len(sitemap_files)}...")
                    except Exception:
                        pass
            else:
                urls = locs
            
            progress_bar.progress(0.5, text=f"✅ {len(urls)} URLs découvertes")
            return sorted(set(urls))
            
        except Exception as e:
            progress_bar.progress(0.5, text="⚠️ Pas de sitemap, scan de la page d'accueil uniquement")
            return [root_url]

def extract_jsonld(html: str) -> list[dict]:
    """Extrait tous les blocs JSON-LD de la page"""
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
    """Vérifie si un schema Product est présent"""
    for d in jsonlds:
        t = d.get("@type")
        if t == "Product" or (isinstance(t, list) and "Product" in t):
            return True
    return False

def analyze_schema_completeness(jsonlds: list[dict]) -> dict:
    """Analyse la complétude des schemas Product"""
    product_schemas = [d for d in jsonlds if d.get("@type") in ["Product", "ProductModel"] or 
                      (isinstance(d.get("@type"), list) and "Product" in d.get("@type"))]
    
    if not product_schemas:
        return {"found": False, "completeness": 0, "missing": []}
    
    product = product_schemas[0]
    
    # Champs essentiels pour les LLMs
    essential_fields = {
        "name": "Nom du produit",
        "description": "Description détaillée",
        "image": "Images",
        "brand": "Marque",
        "offers": "Informations de prix"
    }
    
    # Champs recommandés pour l'enrichissement
    recommended_fields = {
        "sku": "SKU/Référence",
        "gtin": "Code-barres GTIN/EAN",
        "mpn": "Numéro fabricant",
        "aggregateRating": "Note moyenne",
        "review": "Avis clients"
    }
    
    missing = []
    score = 0
    total_points = len(essential_fields) * 2 + len(recommended_fields)
    
    # Vérifier les champs essentiels (2 points chacun)
    for field, label in essential_fields.items():
        if field in product and product[field]:
            score += 2
        else:
            missing.append(f"Champ essentiel : {label}")
    
    # Vérifier les champs recommandés (1 point chacun)
    for field, label in recommended_fields.items():
        if field in product and product[field]:
            score += 1
        else:
            missing.append(f"Champ recommandé : {label}")
    
    # Vérifier la complétude de l'offre si présente
    if "offers" in product:
        offer = product["offers"]
        if isinstance(offer, dict):
            if "price" in offer and "priceCurrency" in offer:
                score += 2
            if "availability" in offer:
                score += 1
                
    completeness = int((score / total_points) * 100)
    
    return {"found": True, "completeness": completeness, "missing": missing, "schema": product}

def analyze_content_quality(soup, text) -> dict:
    """Analyse la qualité et la structure du contenu"""
    findings = {}
    
    # Longueur du contenu
    word_count = len(text.split())
    findings["word_count"] = word_count
    findings["sufficient_content"] = word_count >= 300
    
    # Structure des titres
    h1 = soup.find_all("h1")
    h2 = soup.find_all("h2")
    h3 = soup.find_all("h3")
    findings["has_h1"] = len(h1) > 0
    findings["has_hierarchy"] = len(h2) > 0 or len(h3) > 0
    findings["heading_count"] = len(h1) + len(h2) + len(h3)
    
    # Listes et tableaux
    findings["has_lists"] = len(soup.find_all(["ul", "ol"])) > 0
    findings["has_tables"] = len(soup.find_all("table")) > 0
    findings["list_count"] = len(soup.find_all(["ul", "ol"]))
    findings["table_count"] = len(soup.find_all("table"))
    
    # Contenu structuré pour LLMs
    findings["has_paragraphs"] = len(soup.find_all("p")) >= 3
    
    # Détection de contenu comparatif/contexte
    comparison_keywords = ["vs", "versus", "comparaison", "compare", "difference", "meilleur", "top"]
    findings["has_comparison"] = any(kw in text for kw in comparison_keywords)
    
    usage_keywords = ["utilisation", "usage", "comment utiliser", "mode d'emploi", "guide", "tutoriel"]
    findings["has_usage_guide"] = any(kw in text for kw in usage_keywords)
    
    specs_keywords = ["caractéristiques", "spécifications", "specs", "techniques", "dimensions", "poids", "matière"]
    findings["has_specs"] = any(kw in text for kw in specs_keywords)
    
    return findings

def analyze_authority_signals(soup, text) -> dict:
    """Analyse les signaux d'autorité et crédibilité"""
    findings = {}
    
    # Informations sur l'auteur/marque
    findings["has_author"] = bool(soup.find(attrs={"rel": "author"})) or "par " in text.lower()
    
    # Dates de publication/mise à jour
    time_tags = soup.find_all("time")
    findings["has_publish_date"] = len(time_tags) > 0
    
    # Certifications et garanties
    cert_keywords = ["certifié", "certification", "norme", "garantie", "warranty", "iso", "ce"]
    findings["has_certifications"] = any(kw in text for kw in cert_keywords)
    
    # Informations contact/entreprise
    contact_keywords = ["contact", "téléphone", "email", "adresse", "siret"]
    findings["has_contact_info"] = any(kw in text for kw in contact_keywords)
    
    # Liens externes/sources
    external_links = soup.find_all("a", href=True)
    findings["has_external_links"] = any(link.get("rel") == ["nofollow"] for link in external_links)
    
    return findings

def analyze_metadata(soup) -> dict:
    """Analyse les métadonnées de la page"""
    findings = {}
    
    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    findings["has_meta_description"] = meta_desc is not None and len(meta_desc.get("content", "")) > 50
    
    # Open Graph
    og_tags = soup.find_all("meta", attrs={"property": lambda x: x and x.startswith("og:")})
    findings["has_open_graph"] = len(og_tags) >= 3
    
    # Twitter Cards
    twitter_tags = soup.find_all("meta", attrs={"name": lambda x: x and x.startswith("twitter:")})
    findings["has_twitter_cards"] = len(twitter_tags) >= 2
    
    # Title tag
    title = soup.find("title")
    findings["has_title"] = title is not None and 30 <= len(title.text) <= 70
    
    # Canonical
    findings["has_canonical"] = soup.find("link", attrs={"rel": "canonical"}) is not None
    
    return findings

def analyze_faq_schema(jsonlds: list[dict]) -> dict:
    """Analyse la présence et qualité du schema FAQPage"""
    faq_schemas = [d for d in jsonlds if d.get("@type") == "FAQPage"]
    
    if not faq_schemas:
        return {"found": False, "question_count": 0}
    
    faq = faq_schemas[0]
    questions = faq.get("mainEntity", [])
    
    return {
        "found": True,
        "question_count": len(questions) if isinstance(questions, list) else 1,
        "well_structured": len(questions) >= 5 if isinstance(questions, list) else False
    }

def score_page(html: str) -> dict:
    """Analyse approfondie orientée GEO/LLM avec scoring par catégorie"""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()
    
    # Extraction des données structurées
    jsonlds = extract_jsonld(html)
    
    # === ANALYSE PAR CATÉGORIE ===
    
    # 1. DONNÉES STRUCTURÉES (40 points max)
    schema_analysis = analyze_schema_completeness(jsonlds)
    faq_analysis = analyze_faq_schema(jsonlds)
    
    structured_data_score = 0
    structured_data_findings = {}
    
    if schema_analysis["found"]:
        structured_data_score += int(schema_analysis["completeness"] * 0.25)  # Max 25 points
        structured_data_findings["product_schema_completeness"] = schema_analysis["completeness"]
    
    if faq_analysis["found"]:
        structured_data_score += 10 if faq_analysis["well_structured"] else 5
        structured_data_findings["faq_questions"] = faq_analysis["question_count"]
    
    # Autres schemas utiles
    has_breadcrumb = any(d.get("@type") == "BreadcrumbList" for d in jsonlds)
    if has_breadcrumb:
        structured_data_score += 5
        
    structured_data_findings["has_product_schema"] = schema_analysis["found"]
    structured_data_findings["has_faq_schema"] = faq_analysis["found"]
    structured_data_findings["has_breadcrumb"] = has_breadcrumb
    
    # 2. QUALITÉ DU CONTENU (30 points max)
    content_quality = analyze_content_quality(soup, text)
    
    content_score = 0
    if content_quality["sufficient_content"]:
        content_score += 10
    if content_quality["has_h1"] and content_quality["has_hierarchy"]:
        content_score += 5
    if content_quality["has_lists"]:
        content_score += 3
    if content_quality["has_tables"]:
        content_score += 5
    if content_quality["has_comparison"]:
        content_score += 3
    if content_quality["has_usage_guide"]:
        content_score += 2
    if content_quality["has_specs"]:
        content_score += 2
    
    # 3. AUTORITÉ & CRÉDIBILITÉ (15 points max)
    authority = analyze_authority_signals(soup, text)
    
    authority_score = 0
    if authority["has_author"]:
        authority_score += 3
    if authority["has_publish_date"]:
        authority_score += 3
    if authority["has_certifications"]:
        authority_score += 5
    if authority["has_contact_info"]:
        authority_score += 4
    
    # 4. MÉTADONNÉES (15 points max)
    metadata = analyze_metadata(soup)
    
    metadata_score = 0
    if metadata["has_meta_description"]:
        metadata_score += 5
    if metadata["has_open_graph"]:
        metadata_score += 4
    if metadata["has_twitter_cards"]:
        metadata_score += 2
    if metadata["has_title"]:
        metadata_score += 2
    if metadata["has_canonical"]:
        metadata_score += 2
    
    # SCORE TOTAL (100 points max)
    total_score = structured_data_score + content_score + authority_score + metadata_score
    
    # === GÉNÉRATION DES RECOMMANDATIONS PRIORISÉES ===
    recommendations = []
    
    # Recommandations critiques (impact élevé sur LLMs)
    if not schema_analysis["found"]:
        recommendations.append({
            "priority": "🔴 CRITIQUE",
            "category": "Données Structurées",
            "text": "Ajouter schema.org Product complet avec name, description, image, brand, offers (prix + devise + disponibilité)",
            "impact": "Très élevé - Les LLMs s'appuient massivement sur ces données"
        })
    elif schema_analysis["completeness"] < 70:
        recommendations.append({
            "priority": "🔴 CRITIQUE", 
            "category": "Données Structurées",
            "text": f"Compléter le schema Product (complétude: {schema_analysis['completeness']}%). Manquants: {', '.join(schema_analysis['missing'][:3])}",
            "impact": "Très élevé"
        })
    
    if not content_quality["sufficient_content"]:
        recommendations.append({
            "priority": "🔴 CRITIQUE",
            "category": "Contenu",
            "text": f"Enrichir le contenu ({content_quality['word_count']} mots actuellement, minimum 300-500 mots recommandé)",
            "impact": "Très élevé - Les LLMs ont besoin de contexte"
        })
    
    # Recommandations importantes
    if not faq_analysis["found"] or not faq_analysis["well_structured"]:
        recommendations.append({
            "priority": "🟠 IMPORTANT",
            "category": "Données Structurées",
            "text": "Ajouter une FAQ structurée (minimum 8-12 questions) avec schema FAQPage",
            "impact": "Élevé - Les LLMs adorent les Q&R structurées"
        })
    
    if not content_quality["has_tables"] and not content_quality["has_specs"]:
        recommendations.append({
            "priority": "🟠 IMPORTANT",
            "category": "Contenu",
            "text": "Ajouter un tableau de spécifications techniques détaillé (dimensions, matériaux, normes, poids, etc.)",
            "impact": "Élevé - Facilite les comparaisons par les LLMs"
        })
    
    if not content_quality["has_hierarchy"]:
        recommendations.append({
            "priority": "🟠 IMPORTANT",
            "category": "Contenu",
            "text": "Structurer le contenu avec des titres H2/H3 (ex: Caractéristiques, Utilisation, Avantages)",
            "impact": "Élevé - Aide les LLMs à comprendre la structure"
        })
    
    if not metadata["has_meta_description"]:
        recommendations.append({
            "priority": "🟠 IMPORTANT",
            "category": "Métadonnées",
            "text": "Ajouter une meta description concise (150-160 caractères) avec les infos clés du produit",
            "impact": "Élevé"
        })
    
    # Recommandations recommandées
    if not authority["has_certifications"]:
        recommendations.append({
            "priority": "🟡 RECOMMANDÉ",
            "category": "Autorité",
            "text": "Mentionner les certifications, normes, garanties (ISO, CE, garantie 2 ans, etc.)",
            "impact": "Moyen - Renforce la crédibilité"
        })
    
    if not content_quality["has_comparison"]:
        recommendations.append({
            "priority": "🟡 RECOMMANDÉ",
            "category": "Contenu",
            "text": "Ajouter une section comparative (vs produits similaires, cas d'usage différents)",
            "impact": "Moyen - Aide aux recommandations contextuelles"
        })
    
    if not has_breadcrumb:
        recommendations.append({
            "priority": "🟡 RECOMMANDÉ",
            "category": "Données Structurées",
            "text": "Ajouter un fil d'Ariane avec schema BreadcrumbList",
            "impact": "Moyen - Contexte de navigation"
        })
    
    if not authority["has_author"] or not authority["has_publish_date"]:
        recommendations.append({
            "priority": "🟡 RECOMMANDÉ",
            "category": "Autorité",
            "text": "Indiquer la date de publication/mise à jour et l'auteur/source",
            "impact": "Moyen - Indicateur de fraîcheur"
        })
    
    # Bonus
    if content_quality["has_lists"]:
        recommendations.append({
            "priority": "🟢 BONUS",
            "category": "Contenu",
            "text": "Continuer à utiliser des listes à puces - excellent pour la lisibilité par LLMs",
            "impact": "Faible - Déjà bien fait"
        })
    
    if not content_quality["has_usage_guide"]:
        recommendations.append({
            "priority": "🟢 BONUS",
            "category": "Contenu",
            "text": "Ajouter un guide d'utilisation ou mode d'emploi",
            "impact": "Faible - Enrichissement contextuel"
        })
    
    if not metadata["has_open_graph"]:
        recommendations.append({
            "priority": "🟢 BONUS",
            "category": "Métadonnées",
            "text": "Ajouter les balises Open Graph (og:title, og:description, og:image)",
            "impact": "Faible - Meilleur partage social"
        })
    
    return {
        "score": total_score,
        "score_breakdown": {
            "structured_data": structured_data_score,
            "content_quality": content_score,
            "authority": authority_score,
            "metadata": metadata_score
        },
        "findings": {
            "structured_data": structured_data_findings,
            "content_quality": content_quality,
            "authority": authority,
            "metadata": metadata
        },
        "recommendations": recommendations,
        "schema_analysis": schema_analysis
    }

async def run_audit(root_url: str, max_pages: int, include_pattern: str, 
                   exclude_patterns: list, progress_bar, status_text, selected_categories: list = None, category_level: str = 'level1'):
    """Lance l'audit complet du site"""
    
    # Découverte des URLs
    urls = await discover_urls(root_url, progress_bar)
    
    # Debug : échantillon d'URLs découvertes
    sample_urls = urls[:5] if len(urls) > 0 else []
    
    # Filtrage avec debug
    urls_discovered = len(urls)
    urls_before_domain = urls.copy()
    urls = [u for u in urls if same_domain(u, root_url)]
    urls_after_domain = len(urls)
    
    # NOUVEAU : Filtrage par catégories si sélectionnées
    urls_after_categories = urls_after_domain
    if selected_categories and "Toutes les catégories" not in selected_categories:
        urls = filter_urls_by_categories(urls, selected_categories, category_level)
        urls_after_categories = len(urls)
        status_text.text(f"📁 Filtrage par catégories ({category_level}) : {urls_after_domain} → {urls_after_categories} URLs")
    
    # Debug : pourquoi certaines URLs sont rejetées
    rejected_by_domain = []
    if urls_after_domain < urls_discovered:
        for u in urls_before_domain[:5]:
            if not same_domain(u, root_url):
                url_domain = urlparse(u).netloc.lower().replace('www.', '')
                root_domain = urlparse(root_url).netloc.lower().replace('www.', '')
                rejected_by_domain.append(f"{u} (domaine: {url_domain} vs {root_domain})")
    
    urls = [u for u in urls if url_allowed(u, include_pattern, exclude_patterns)]
    urls_after_patterns = len(urls)
    urls = urls[:max_pages]
    
    # Vérification si on a des URLs à analyser
    if len(urls) == 0:
        suggestions = [
            f"📊 **Debug** : {urls_discovered} URLs découvertes → {urls_after_domain} après filtre domaine",
        ]
        
        if selected_categories and "Toutes les catégories" not in selected_categories:
            suggestions.append(f"📁 → {urls_after_categories} après filtre catégories ({', '.join(selected_categories)})")
        
        suggestions.append(f"🔍 → {urls_after_patterns} après filtres patterns")
        suggestions.append(f"🌐 **Domaine root** : `{urlparse(root_url).netloc}` (sans www: `{urlparse(root_url).netloc.lower().replace('www.', '')}`)")
        
        if sample_urls:
            suggestions.append(f"🔗 **Échantillon d'URLs trouvées** :")
            for url in sample_urls[:3]:
                suggestions.append(f"   - {url}")
        
        if rejected_by_domain:
            suggestions.append(f"❌ **URLs rejetées par filtre domaine** :")
            for rej in rejected_by_domain[:3]:
                suggestions.append(f"   - {rej}")
        
        suggestions.extend([
            f"🔍 **Pattern inclusion** : `{include_pattern if include_pattern else '(aucun)'}`",
            f"🚫 **Patterns exclusion** : `{exclude_patterns if exclude_patterns else '(aucun)'}`",
            "💡 **Solution** : Copiez une URL du sitemap ci-dessus et utilisez-la directement"
        ])
        
        return {
            "error": True,
            "message": f"Aucune URL trouvée après filtrage.",
            "suggestions": suggestions
        }
    
    status_text.text(f"📊 Analyse de {len(urls)} page(s)...")
    
    results = []
    
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "LLM-Product-Auditor/1.0"}
    ) as client:
        
        for i, url in enumerate(urls):
            try:
                progress = 0.5 + (i / len(urls)) * 0.5
                progress_bar.progress(progress, text=f"🔍 Analyse {i+1}/{len(urls)}...")
                
                r = await client.get(url, timeout=20)
                ct = r.headers.get("content-type", "")
                
                if "text/html" in ct:
                    html = r.text
                    data = score_page(html)
                    page_type = "product" if data["findings"].get("has_product_schema") else "other"
                    
                    results.append({
                        "url": url,
                        "status": r.status_code,
                        "type": page_type,
                        "score": data["score"],
                        "findings": data["findings"],
                        "recommendations": data["recommendations"]
                    })
                else:
                    results.append({
                        "url": url,
                        "status": r.status_code,
                        "type": "error",
                        "score": 0,
                        "findings": {},
                        "recommendations": ["⚠️ Page non HTML"]
                    })
                    
            except Exception as e:
                results.append({
                    "url": url,
                    "status": 0,
                    "type": "error",
                    "score": 0,
                    "findings": {},
                    "recommendations": [f"❌ Erreur: {str(e)[:50]}"]
                })
    
    # Tri : produits d'abord, puis par score croissant (pages à améliorer en priorité)
    results.sort(key=lambda x: (0 if x["type"] == "product" else 1, x["score"]))
    
    progress_bar.progress(1.0, text="✅ Analyse terminée !")
    
    return results

# Interface principale
root_url = st.text_input(
    "🌐 URL à auditer",
    value="https://www.vetdepro.com",
    help="Entrez soit l'URL du site complet (ex: https://monsite.com) soit l'URL d'une page produit spécifique",
    placeholder="Ex: https://www.monsite.com ou https://www.monsite.com/produit/chaussures"
)

# Bouton pour détecter les catégories
col_detect, col_info = st.columns([1, 3])

with col_detect:
    detect_categories_btn = st.button("🔍 Détecter les catégories", use_container_width=True)

with col_info:
    if 'categories' in st.session_state and st.session_state.categories:
        st.info(f"✅ {len(st.session_state.categories)} catégorie(s) détectée(s)")

# Détection des catégories
if detect_categories_btn and root_url:
    with st.spinner("🔍 Analyse du sitemap pour détecter les catégories..."):
        try:
            import asyncio
            
            # Créer une progress bar temporaire
            temp_progress = st.progress(0)
            
            # Découvrir les URLs du sitemap
            urls = asyncio.run(discover_urls(root_url, temp_progress))
            temp_progress.empty()
            
            # Extraire les catégories (nouvelle logique)
            parsed = urlparse(root_url)
            base_domain = parsed.netloc
            categories_result = extract_categories_from_urls(urls, base_domain)
            
            level1_cats = categories_result['level1']
            level2_cats = categories_result['level2']
            all_segments = categories_result['all_segments']
            category_pages = categories_result.get('category_pages', [])
            short_urls = categories_result.get('short_urls', 0)
            long_urls = categories_result.get('long_urls', 0)
            
            # TOUJOURS AFFICHER LE DEBUG
            st.success(f"✅ Analyse terminée : {len(urls)} URLs trouvées")
            st.info(f"📊 **{short_urls} pages catégories** (URLs courtes) • **{long_urls} pages produits** (URLs longues)")
            
            with st.expander("🔍 DEBUG - Structure du site", expanded=True):
                tab1, tab2, tab3 = st.tabs(["📁 Pages Catégories", "📦 Pages Produits", "📊 Statistiques"])
                
                with tab1:
                    st.markdown("### Pages Catégories détectées (URLs courtes)")
                    st.caption("Ce sont les pages qui regroupent plusieurs produits")
                    
                    if category_pages:
                        for url, cat_name in category_pages[:20]:
                            st.markdown(f"**{cat_name}**")
                            st.code(url, language=None)
                        
                        if len(category_pages) > 20:
                            st.caption(f"... et {len(category_pages) - 20} autres pages catégories")
                    else:
                        st.warning("Aucune page catégorie détectée (URLs courtes)")
                
                with tab2:
                    st.markdown("### Exemple de pages Produits (URLs longues)")
                    st.caption("Ces URLs contiennent les catégories dans leurs segments")
                    
                    long_url_examples = [(url, segs, nb) for url, segs, nb in all_segments if nb >= 3][:10]
                    
                    if long_url_examples:
                        for url, segments, nb_segs in long_url_examples:
                            st.code(url, language=None)
                            st.write(f"→ **Segments:** {' / '.join(segments)} ({nb_segs} segments)")
                            st.markdown("---")
                    else:
                        st.info("Pas de pages produits détectées")
                
                with tab3:
                    st.markdown("### Statistiques globales")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.metric("Total URLs", len(urls))
                        st.metric("Pages catégories (≤2 segments)", short_urls)
                        st.metric("Pages produits (≥3 segments)", long_urls)
                    
                    with col2:
                        st.metric("Catégories Niveau 1", len(level1_cats))
                        st.metric("Catégories Niveau 2", len(level2_cats))
            
            # Afficher les catégories détectées
            if level1_cats or level2_cats:
                with st.expander("📁 Catégories disponibles pour filtrage", expanded=True):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("#### 📁 Niveau 1 - Pages Catégories")
                        st.caption("Basées sur les URLs courtes (pages catégories)")
                        if level1_cats:
                            for cat, count in list(level1_cats.items())[:30]:
                                st.write(f"• **{cat}** : {count} URLs")
                            if len(level1_cats) > 30:
                                st.caption(f"... et {len(level1_cats) - 30} autre(s)")
                        else:
                            st.warning("Aucune catégorie niveau 1")
                    
                    with col2:
                        st.markdown("#### 📂 Niveau 2 - Catégories Produits")
                        st.caption("Extraites depuis les URLs produits (pages longues)")
                        if level2_cats:
                            for cat, count in list(level2_cats.items())[:30]:
                                st.write(f"• **{cat}** : {count} URLs")
                            if len(level2_cats) > 30:
                                st.caption(f"... et {len(level2_cats) - 30} autre(s)")
                        else:
                            st.warning("Aucune catégorie niveau 2")
                
                # Stocker et continuer
                st.session_state.categories = categories_result
                st.session_state.discovered_urls = urls
                
                # Message personnalisé selon ce qui est détecté
                if level1_cats and level2_cats:
                    st.success(f"✨ **{len(level1_cats)} pages catégories** et **{len(level2_cats)} catégories produits** détectées !")
                    st.info("""
                    **💡 Recommandation** :
                    - **Niveau 1** : Pour scanner les PAGES CATÉGORIES elles-mêmes (ex: /5-pantalon-de-travail)
                    - **Niveau 2** : Pour scanner les PAGES PRODUITS d'une catégorie (ex: tous les produits de "5-pantalon-de-travail")
                    """)
                elif level1_cats:
                    st.success(f"✨ **{len(level1_cats)} pages catégories** détectées !")
                    st.info("💡 Utilisez **Niveau 1** pour scanner ces pages catégories")
                elif level2_cats:
                    st.success(f"✨ **{len(level2_cats)} catégories produits** détectées !")
                    st.info("💡 Utilisez **Niveau 2** pour filtrer les produits par catégorie")
                
                st.rerun()
            else:
                st.error("❌ Aucune catégorie détectée")
                st.warning("""
                **Regardez les onglets DEBUG ci-dessus !**
                
                Si vous voyez des URLs mais aucune catégorie détectée :
                1. Utilisez les **filtres templates** (Shopify, PrestaShop, etc.)
                2. Utilisez les **patterns personnalisés**
                3. Scannez directement une **URL de page produit**
                """)
                st.session_state.categories = {}
                
        except Exception as e:
            st.error(f"❌ Erreur lors de la détection : {str(e)}")
            import traceback
            st.code(traceback.format_exc(), language="python")
            st.session_state.categories = {}

# Multiselect des catégories si détectées
selected_categories = []
category_level = 'level1'

if 'categories' in st.session_state and st.session_state.categories:
    categories_result = st.session_state.categories
    level1_cats = categories_result.get('level1', {})
    level2_cats = categories_result.get('level2', {})
    
    st.markdown("### 📁 Filtrer par catégories")
    
    # Choix du niveau
    has_level1 = len(level1_cats) > 0
    has_level2 = len(level2_cats) > 0
    
    if has_level1 and has_level2:
        category_level = st.radio(
            "Que voulez-vous scanner ?",
            options=['level1', 'level2'],
            format_func=lambda x: f"📁 Pages Catégories ({len(level1_cats)} pages) - Scanner les pages de catégories elles-mêmes" if x == 'level1' else f"📦 Pages Produits ({len(level2_cats)} catégories) - Scanner les produits filtrés par catégorie",
            horizontal=False,
            help="Niveau 1 = scanner les pages catégories (ex: /5-pantalon). Niveau 2 = scanner les produits d'une catégorie (ex: tous les produits avec '5-pantalon' dans l'URL)"
        )
    elif has_level1:
        category_level = 'level1'
        st.info(f"📁 Mode : Pages Catégories ({len(level1_cats)} pages détectées)")
    elif has_level2:
        category_level = 'level2'
        st.info(f"📦 Mode : Pages Produits ({len(level2_cats)} catégories détectées)")
    
    # Afficher les options selon le niveau
    if category_level == 'level1' and level1_cats:
        st.markdown("#### Scanner les pages catégories suivantes :")
        
        category_options = ["Toutes les catégories"] + [
            f"{cat} ({count} page(s))" for cat, count in level1_cats.items()
        ]
        
        selected = st.multiselect(
            "Sélectionnez les catégories",
            options=category_options,
            default=["Toutes les catégories"],
            help="Ces pages sont les pages de catégories (ex: /5-pantalon-de-travail). Sélectionnez celles que vous voulez auditer."
        )
        
        if "Toutes les catégories" not in selected:
            selected_categories = [s.split(" (")[0] for s in selected if " (" in s]
        else:
            selected_categories = ["Toutes les catégories"]
        
        if selected_categories and "Toutes les catégories" not in selected_categories:
            total_urls = sum(level1_cats.get(cat, 0) for cat in selected_categories)
            st.info(f"📊 **{total_urls} page(s) catégorie** seront scannées")
    
    elif category_level == 'level2' and level2_cats:
        st.markdown("#### Scanner les pages produits des catégories suivantes :")
        
        category_options = ["Toutes les catégories"] + [
            f"{cat} ({count} produit(s))" for cat, count in level2_cats.items()
        ]
        
        selected = st.multiselect(
            "Sélectionnez les catégories",
            options=category_options,
            default=["Toutes les catégories"],
            help="Filtrer les pages produits par catégorie (ex: tous les produits avec '5-pantalon-de-travail' dans l'URL)"
        )
        
        if "Toutes les catégories" not in selected:
            selected_categories = [s.split(" (")[0] for s in selected if " (" in s]
        else:
            selected_categories = ["Toutes les catégories"]
        
        if selected_categories and "Toutes les catégories" not in selected_categories:
            total_urls = sum(level2_cats.get(cat, 0) for cat in selected_categories)
            st.info(f"📊 **{total_urls} page(s) produit** seront analysées")

col1, col2 = st.columns([1, 4])

with col1:
    scan_button = st.button("🚀 Lancer le scan", type="primary", use_container_width=True)

with col2:
    with st.expander("💡 Exemples d'utilisation"):
        st.markdown("""
        **Cas 1 : Scanner tout un site e-commerce**
        - URL : `https://www.monsite.com`
        - Template : Shopify / PrestaShop / etc.
        - Résultat : Analyse toutes les pages produits du site
        
        **Cas 2 : Scanner une page produit spécifique**
        - URL : `https://www.monsite.com/produit/chaussures-123`
        - Template : Aucun filtre (ou Personnalisé sans filtre)
        - Résultat : Analyse uniquement cette page
        
        **Cas 3 : Si aucune page n'est trouvée**
        - Sélectionnez "Aucun filtre" dans la sidebar
        - Ou entrez directement l'URL de la page produit
        """)

if scan_button:
    if not root_url:
        st.error("⚠️ Veuillez entrer une URL")
    else:
        # S'assurer que selected_categories et category_level existent
        if 'selected_categories' not in locals():
            selected_categories = []
        if 'category_level' not in locals():
            category_level = 'level1'
        
        # Debug : afficher les filtres actifs
        with st.expander("🔧 Debug - Filtres actifs", expanded=False):
            st.write(f"**Template sélectionné** : {template}")
            st.write(f"**Pattern d'inclusion** : `{include_pattern if include_pattern else '(aucun)'}`")
            st.write(f"**Patterns d'exclusion** : `{exclude_patterns if exclude_patterns else '(aucun)'}`")
            if selected_categories and "Toutes les catégories" not in selected_categories:
                st.write(f"**Catégories sélectionnées ({category_level})** : {', '.join(selected_categories)}")
            else:
                st.write(f"**Catégories** : Toutes (aucun filtre)")
        
        # Affichage de la progression
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            # Lance l'audit
            import asyncio
            results = asyncio.run(run_audit(
                root_url, 
                max_pages, 
                include_pattern, 
                exclude_patterns,
                progress_bar,
                status_text,
                selected_categories,  # Filtrage par catégories
                category_level  # Niveau de filtrage
            ))
            
            status_text.empty()
            progress_bar.empty()
            
            # Vérifier si c'est une erreur
            if isinstance(results, dict) and results.get("error"):
                st.error(f"⚠️ {results['message']}")
                st.info("💡 **Suggestions :**")
                for suggestion in results.get("suggestions", []):
                    st.write(suggestion)
            else:
                # Stocke les résultats dans la session
                st.session_state.results = results
                st.session_state.root_url = root_url
                
                # Statistiques globales avec style moderne
                total = len(results)
                products = len([r for r in results if r["type"] == "product"])
                avg_score = sum(r["score"] for r in results) / total if total > 0 else 0
                to_optimize = len([r for r in results if r["score"] < 70])
                
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("### 📊 Aperçu de l'audit")
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.markdown(f"""
                    <div style='background: linear-gradient(135deg, #ede9fe 0%, #f3f4f6 100%); padding: 1.5rem; border-radius: 16px; border-left: 4px solid #6366f1; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);'>
                        <div style='color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>PAGES ANALYSÉES</div>
                        <div style='font-size: 2.5rem; font-weight: 800; background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>{total}</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                    <div style='background: linear-gradient(135deg, #d1fae5 0%, #ecfdf5 100%); padding: 1.5rem; border-radius: 16px; border-left: 4px solid #10b981; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);'>
                        <div style='color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>PAGES PRODUITS</div>
                        <div style='font-size: 2.5rem; font-weight: 800; color: #10b981;'>{products}</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col3:
                    score_color = "#10b981" if avg_score >= 70 else "#f59e0b" if avg_score >= 40 else "#ef4444"
                    score_bg = "linear-gradient(135deg, #d1fae5 0%, #ecfdf5 100%)" if avg_score >= 70 else "linear-gradient(135deg, #fef3c7 0%, #fef9e7 100%)" if avg_score >= 40 else "linear-gradient(135deg, #fee2e2 0%, #fef2f2 100%)"
                    st.markdown(f"""
                    <div style='background: {score_bg}; padding: 1.5rem; border-radius: 16px; border-left: 4px solid {score_color}; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);'>
                        <div style='color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>SCORE MOYEN</div>
                        <div style='font-size: 2.5rem; font-weight: 800; color: {score_color};'>{avg_score:.0f}<span style='font-size: 1.5rem; color: #94a3b8;'>/100</span></div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col4:
                    st.markdown(f"""
                    <div style='background: linear-gradient(135deg, #fed7aa 0%, #ffedd5 100%); padding: 1.5rem; border-radius: 16px; border-left: 4px solid #ea580c; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);'>
                        <div style='color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>À OPTIMISER</div>
                        <div style='font-size: 2.5rem; font-weight: 800; color: #ea580c;'>{to_optimize}</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.success(f"✅ Scan terminé ! {total} page(s) analysée(s) • {products} page(s) produit détectée(s)")
            
        except Exception as e:
            st.error(f"❌ Erreur lors du scan : {str(e)}")
            progress_bar.empty()
            status_text.empty()

# Affichage des résultats
if "results" in st.session_state and st.session_state.results:
    results = st.session_state.results
    
    st.markdown("---")
    st.subheader("📊 Résultats détaillés")
    
    # Filtres
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_type = st.selectbox("Type", ["Tous", "product", "other", "error"])
    with col2:
        filter_score = st.slider("Score minimum", 0, 100, 0)
    with col3:
        export_csv = st.button("💾 Exporter en CSV", use_container_width=True)
    
    # Application des filtres
    filtered_results = results
    if filter_type != "Tous":
        filtered_results = [r for r in filtered_results if r["type"] == filter_type]
    filtered_results = [r for r in filtered_results if r["score"] >= filter_score]
    
    # Export CSV
    if export_csv:
        df_export = pd.DataFrame([
            {
                "URL": r["url"],
                "Type": r["type"],
                "Status": r["status"],
                "Score Total": r["score"],
                "Score Données Structurées": r.get("score_breakdown", {}).get("structured_data", 0),
                "Score Contenu": r.get("score_breakdown", {}).get("content_quality", 0),
                "Score Autorité": r.get("score_breakdown", {}).get("authority", 0),
                "Score Métadonnées": r.get("score_breakdown", {}).get("metadata", 0),
                "Nombre Recommandations": len(r["recommendations"]),
                "Recommandations Critiques": len([rec for rec in r["recommendations"] if "CRITIQUE" in rec.get("priority", "")]),
                "Top 3 Recommandations": " | ".join([rec.get("text", rec) if isinstance(rec, dict) else rec for rec in r["recommendations"][:3]])
            }
            for r in filtered_results
        ])
        
        csv = df_export.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Télécharger le rapport CSV",
            data=csv,
            file_name=f"audit_geo_{urlparse(st.session_state.root_url).netloc}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
    
    st.info(f"Affichage de {len(filtered_results)} page(s) sur {len(results)}")
    
    # Tableau des résultats
    for i, r in enumerate(filtered_results):
        # Déterminer la couleur du score
        score_color = "🟢" if r['score'] >= 70 else "🟡" if r['score'] >= 40 else "🔴"
        score_emoji = "🛍️" if r['type'] == 'product' else "📄"
        
        with st.expander(
            f"{score_emoji} **Score: {score_color} {r['score']}/100** - {r['url'][:70]}{'...' if len(r['url']) > 70 else ''}",
            expanded=(i < 3)  # Affiche les 3 premiers
        ):
            # En-tête avec URL et statut
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.caption("🔗 URL")
                st.code(r["url"], language=None)
            
            with col2:
                st.caption("📊 Statut")
                st.write(f"**Type:** {r['type']}")
                st.write(f"**HTTP:** {r['status']}")
            
            # Scores par catégorie (si disponibles)
            if "score_breakdown" in r:
                st.markdown("---")
                st.markdown("#### 📊 Scores détaillés")
                
                breakdown = r["score_breakdown"]
                
                # Données structurées
                pct_struct = (breakdown['structured_data'] / 40) * 100
                color_struct = "#10b981" if pct_struct >= 70 else "#f59e0b" if pct_struct >= 40 else "#ef4444"
                st.markdown(f"""
                <div style='margin-bottom: 1rem;'>
                    <div style='display: flex; justify-content: space-between; margin-bottom: 0.25rem;'>
                        <span style='font-weight: 600; color: #0f172a; font-size: 0.875rem;'>🔢 Données Structurées</span>
                        <span style='font-weight: 700; color: {color_struct}; font-size: 0.875rem;'>{breakdown['structured_data']}/40</span>
                    </div>
                    <div style='background: #e2e8f0; height: 8px; border-radius: 9999px; overflow: hidden;'>
                        <div style='background: {color_struct}; height: 100%; width: {pct_struct}%; transition: width 0.3s ease;'></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Contenu
                pct_content = (breakdown['content_quality'] / 30) * 100
                color_content = "#10b981" if pct_content >= 70 else "#f59e0b" if pct_content >= 40 else "#ef4444"
                st.markdown(f"""
                <div style='margin-bottom: 1rem;'>
                    <div style='display: flex; justify-content: space-between; margin-bottom: 0.25rem;'>
                        <span style='font-weight: 600; color: #0f172a; font-size: 0.875rem;'>📝 Qualité Contenu</span>
                        <span style='font-weight: 700; color: {color_content}; font-size: 0.875rem;'>{breakdown['content_quality']}/30</span>
                    </div>
                    <div style='background: #e2e8f0; height: 8px; border-radius: 9999px; overflow: hidden;'>
                        <div style='background: {color_content}; height: 100%; width: {pct_content}%; transition: width 0.3s ease;'></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Autorité
                pct_authority = (breakdown['authority'] / 15) * 100
                color_authority = "#10b981" if pct_authority >= 70 else "#f59e0b" if pct_authority >= 40 else "#ef4444"
                st.markdown(f"""
                <div style='margin-bottom: 1rem;'>
                    <div style='display: flex; justify-content: space-between; margin-bottom: 0.25rem;'>
                        <span style='font-weight: 600; color: #0f172a; font-size: 0.875rem;'>🏆 Autorité</span>
                        <span style='font-weight: 700; color: {color_authority}; font-size: 0.875rem;'>{breakdown['authority']}/15</span>
                    </div>
                    <div style='background: #e2e8f0; height: 8px; border-radius: 9999px; overflow: hidden;'>
                        <div style='background: {color_authority}; height: 100%; width: {pct_authority}%; transition: width 0.3s ease;'></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Métadonnées
                pct_metadata = (breakdown['metadata'] / 15) * 100
                color_metadata = "#10b981" if pct_metadata >= 70 else "#f59e0b" if pct_metadata >= 40 else "#ef4444"
                st.markdown(f"""
                <div style='margin-bottom: 1rem;'>
                    <div style='display: flex; justify-content: space-between; margin-bottom: 0.25rem;'>
                        <span style='font-weight: 600; color: #0f172a; font-size: 0.875rem;'>🏷️ Métadonnées</span>
                        <span style='font-weight: 700; color: {color_metadata}; font-size: 0.875rem;'>{breakdown['metadata']}/15</span>
                    </div>
                    <div style='background: #e2e8f0; height: 8px; border-radius: 9999px; overflow: hidden;'>
                        <div style='background: {color_metadata}; height: 100%; width: {pct_metadata}%; transition: width 0.3s ease;'></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            # Recommandations priorisées
            if r["recommendations"]:
                st.markdown("---")
                st.markdown("#### 💡 Recommandations GEO")
                
                # Grouper par priorité
                critiques = [rec for rec in r["recommendations"] if "CRITIQUE" in rec.get("priority", "")]
                importantes = [rec for rec in r["recommendations"] if "IMPORTANT" in rec.get("priority", "")]
                recommandees = [rec for rec in r["recommendations"] if "RECOMMANDÉ" in rec.get("priority", "")]
                bonus = [rec for rec in r["recommendations"] if "BONUS" in rec.get("priority", "")]
                
                # Afficher par priorité avec style moderne
                if critiques:
                    st.markdown("""
                    <div style='background: linear-gradient(135deg, #fee2e2 0%, #fef2f2 100%); padding: 1rem 1.25rem; border-radius: 12px; border-left: 4px solid #ef4444; margin-bottom: 1rem;'>
                        <div style='font-weight: 700; color: #dc2626; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem;'>
                            🔴 ACTIONS CRITIQUES (à faire en priorité)
                        </div>
                    """, unsafe_allow_html=True)
                    for rec in critiques:
                        st.markdown(f"""
                        <div style='background: white; padding: 0.875rem; border-radius: 8px; margin-bottom: 0.5rem; box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);'>
                            <div style='font-weight: 600; color: #0f172a; margin-bottom: 0.25rem;'>
                                <span style='background: #fee2e2; color: #dc2626; padding: 0.125rem 0.5rem; border-radius: 4px; font-size: 0.75rem; margin-right: 0.5rem;'>{rec['category']}</span>
                                {rec['text']}
                            </div>
                            <div style='color: #64748b; font-size: 0.875rem;'>💥 Impact: {rec['impact']}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
                
                if importantes:
                    st.markdown("""
                    <div style='background: linear-gradient(135deg, #fed7aa 0%, #ffedd5 100%); padding: 1rem 1.25rem; border-radius: 12px; border-left: 4px solid #ea580c; margin-bottom: 1rem;'>
                        <div style='font-weight: 700; color: #ea580c; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem;'>
                            🟠 ACTIONS IMPORTANTES
                        </div>
                    """, unsafe_allow_html=True)
                    for rec in importantes:
                        st.markdown(f"""
                        <div style='background: white; padding: 0.875rem; border-radius: 8px; margin-bottom: 0.5rem; box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);'>
                            <div style='font-weight: 600; color: #0f172a; margin-bottom: 0.25rem;'>
                                <span style='background: #fed7aa; color: #ea580c; padding: 0.125rem 0.5rem; border-radius: 4px; font-size: 0.75rem; margin-right: 0.5rem;'>{rec['category']}</span>
                                {rec['text']}
                            </div>
                            <div style='color: #64748b; font-size: 0.875rem;'>📈 Impact: {rec['impact']}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
                
                if recommandees:
                    with st.expander("🟡 Actions Recommandées (cliquer pour voir)", expanded=False):
                        for rec in recommandees:
                            st.markdown(f"""
                            <div style='background: linear-gradient(135deg, #fef3c7 0%, #fef9e7 100%); padding: 0.875rem; border-radius: 8px; margin-bottom: 0.5rem;'>
                                <div style='font-weight: 600; color: #0f172a; margin-bottom: 0.25rem;'>
                                    <span style='background: #fbbf24; color: white; padding: 0.125rem 0.5rem; border-radius: 4px; font-size: 0.75rem; margin-right: 0.5rem;'>{rec['category']}</span>
                                    {rec['text']}
                                </div>
                                <div style='color: #64748b; font-size: 0.875rem;'>Impact: {rec['impact']}</div>
                            </div>
                            """, unsafe_allow_html=True)
                
                if bonus:
                    with st.expander("🟢 Améliorations Bonus", expanded=False):
                        for rec in bonus:
                            st.markdown(f"""
                            <div style='background: linear-gradient(135deg, #d1fae5 0%, #ecfdf5 100%); padding: 0.875rem; border-radius: 8px; margin-bottom: 0.5rem;'>
                                <div style='font-weight: 600; color: #0f172a; margin-bottom: 0.25rem;'>
                                    <span style='background: #10b981; color: white; padding: 0.125rem 0.5rem; border-radius: 4px; font-size: 0.75rem; margin-right: 0.5rem;'>{rec['category']}</span>
                                    {rec['text']}
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
            
            # Détails techniques (expandable)
            if "findings" in r:
                with st.expander("🔧 Détails techniques", expanded=False):
                    st.json(r["findings"])
else:
    st.info("👆 Entrez une URL et lancez un scan pour commencer l'audit")

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; padding: 20px;'>
    <p>🔍 <strong>LLM Product Page Auditor</strong> - Optimisation GEO pour les moteurs IA génératifs</p>
    <p style='font-size: 0.9em;'>Analyse approfondie orientée LLMs : données structurées complètes, contenu riche, signaux d'autorité et métadonnées optimisées</p>
    <p style='font-size: 0.8em; margin-top: 10px;'>💡 GEO = Generative Engine Optimization - Le nouveau SEO pour ChatGPT, Claude, Perplexity & co.</p>
</div>
""", unsafe_allow_html=True)
