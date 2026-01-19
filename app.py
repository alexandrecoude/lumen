import streamlit as st
import re
import json
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import time
from collections import Counter

st.set_page_config(
    page_title="LLM Product Page Auditor",
    page_icon="🔍",
    layout="wide"
)

# CSS simple
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

st.title("🔍 LLM Product Page Auditor - Gestion des Catégories")
st.markdown("**Étape 1** : Scannez votre site → **Étape 2** : Organisez vos catégories → **Étape 3** : Lancez l'audit ciblé")

# Initialisation session state
if 'all_urls' not in st.session_state:
    st.session_state.all_urls = []
if 'categories_custom' not in st.session_state:
    st.session_state.categories_custom = {}
if 'scan_done' not in st.session_state:
    st.session_state.scan_done = False

SITEMAP_RE = re.compile(r'<loc>(.*?)</loc>', re.IGNORECASE)

def clean_url(url):
    """Nettoie les balises XML comme <![CDATA[ et ]]>"""
    return url.replace('<![CDATA[', '').replace(']]>', '').strip()

async def discover_urls(root_url: str) -> list[str]:
    """Découvre toutes les URLs via sitemap.xml"""
    sitemap_url = urljoin(root_url.rstrip("/") + "/", "sitemap.xml")
    
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "LLM-Product-Auditor/1.0"}
    ) as client:
        try:
            xml = (await client.get(sitemap_url, timeout=20)).text
            locs = SITEMAP_RE.findall(xml)
            locs = [clean_url(loc) for loc in locs]
            urls = []
            
            # Gestion des sitemap index
            sitemap_files = [loc for loc in locs if loc.endswith(".xml") and "sitemap" in loc.lower()]
            
            if sitemap_files:
                for sitemap_file in sitemap_files:
                    try:
                        subxml = (await client.get(sitemap_file, timeout=20)).text
                        sub_locs = SITEMAP_RE.findall(subxml)
                        sub_locs = [clean_url(loc) for loc in sub_locs]
                        urls.extend(sub_locs)
                    except Exception:
                        pass
            else:
                urls = locs
            
            return sorted(set(urls))
            
        except Exception as e:
            st.error(f"Erreur lors du scan du sitemap : {str(e)}")
            return []

def auto_suggest_categories(urls: list[str]) -> dict:
    """
    Auto-suggestion de catégories basée sur l'analyse des URLs.
    Retourne un dict {nom_categorie: [liste_urls]}
    """
    suggestions = {}
    
    # Analyser les patterns d'URLs
    url_patterns = {}
    
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        segments = [s for s in path.split('/') if s]
        
        # Compter combien de segments
        nb_segments = len(segments)
        
        # URLs courtes (1-2 segments) = potentiellement des catégories
        if nb_segments <= 2 and nb_segments >= 1:
            first_seg = segments[0]
            # Créer une catégorie si ce segment apparaît souvent
            if first_seg not in url_patterns:
                url_patterns[first_seg] = []
            url_patterns[first_seg].append(url)
        
        # URLs longues (3+ segments) = potentiellement des produits
        elif nb_segments >= 3:
            # Chercher un segment qui ressemble à une catégorie
            for seg in segments:
                if len(seg) > 3 and not seg.isdigit():
                    # Ignorer les segments génériques
                    if seg.lower() not in ['content', 'product', 'produit', 'item', 'article']:
                        if seg not in url_patterns:
                            url_patterns[seg] = []
                        url_patterns[seg].append(url)
                        break
    
    # Créer les suggestions (seulement celles avec 2+ URLs)
    for category, category_urls in url_patterns.items():
        if len(category_urls) >= 2:
            suggestions[category] = category_urls
    
    return suggestions

# ÉTAPE 1 : SCAN DU SITEMAP
st.markdown("## 📡 Étape 1 : Scanner le sitemap")

root_url = st.text_input(
    "URL du site à scanner",
    value="https://www.vetdepro.com",
    help="L'outil va scanner le sitemap.xml pour découvrir toutes les URLs"
)

if st.button("🚀 Scanner le sitemap complet", type="primary"):
    if root_url:
        with st.spinner("🔍 Scan du sitemap en cours..."):
            import asyncio
            urls = asyncio.run(discover_urls(root_url))
            
            if urls:
                st.session_state.all_urls = urls
                st.session_state.scan_done = True
                
                # Auto-suggestion de catégories
                suggested = auto_suggest_categories(urls)
                st.session_state.categories_custom = suggested
                
                st.success(f"✅ {len(urls)} URLs découvertes !")
                st.info(f"💡 {len(suggested)} catégories auto-détectées (vous pouvez les modifier ci-dessous)")
                st.rerun()
            else:
                st.error("❌ Aucune URL trouvée dans le sitemap")
    else:
        st.error("⚠️ Veuillez entrer une URL")

# Afficher les URLs scannées
if st.session_state.scan_done and st.session_state.all_urls:
    st.markdown("---")
    st.markdown(f"### ✅ {len(st.session_state.all_urls)} URLs découvertes")
    
    with st.expander("📋 Voir toutes les URLs", expanded=False):
        # Grouper par nombre de segments
        short_urls = []
        medium_urls = []
        long_urls = []
        
        for url in st.session_state.all_urls:
            parsed = urlparse(url)
            segments = [s for s in parsed.path.strip('/').split('/') if s]
            nb = len(segments)
            
            if nb <= 2:
                short_urls.append(url)
            elif nb == 3:
                medium_urls.append(url)
            else:
                long_urls.append(url)
        
        tab1, tab2, tab3 = st.tabs([
            f"📁 URLs courtes ({len(short_urls)})",
            f"📄 URLs moyennes ({len(medium_urls)})",
            f"📦 URLs longues ({len(long_urls)})"
        ])
        
        with tab1:
            st.caption("URLs avec 1-2 segments (probablement des pages catégories)")
            for url in short_urls[:50]:
                st.code(url, language=None)
            if len(short_urls) > 50:
                st.caption(f"... et {len(short_urls) - 50} autres")
        
        with tab2:
            st.caption("URLs avec 3 segments")
            for url in medium_urls[:50]:
                st.code(url, language=None)
            if len(medium_urls) > 50:
                st.caption(f"... et {len(medium_urls) - 50} autres")
        
        with tab3:
            st.caption("URLs avec 4+ segments (probablement des pages produits)")
            for url in long_urls[:50]:
                st.code(url, language=None)
            if len(long_urls) > 50:
                st.caption(f"... et {len(long_urls) - 50} autres")

# ÉTAPE 2 : GESTION DES CATÉGORIES
if st.session_state.scan_done:
    st.markdown("---")
    st.markdown("## 📁 Étape 2 : Gérer vos catégories")
    
    tab_manage, tab_create, tab_export = st.tabs([
        "📝 Éditer les catégories",
        "➕ Créer une catégorie",
        "💾 Exporter/Importer"
    ])
    
    with tab_manage:
        st.markdown("### Catégories actuelles")
        
        if not st.session_state.categories_custom:
            st.info("Aucune catégorie définie. Utilisez l'onglet 'Créer une catégorie' pour commencer.")
        else:
            # Afficher chaque catégorie
            for cat_name in list(st.session_state.categories_custom.keys()):
                cat_urls = st.session_state.categories_custom[cat_name]
                
                with st.expander(f"📁 {cat_name} ({len(cat_urls)} URLs)", expanded=False):
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        # Afficher les URLs de cette catégorie
                        st.caption("URLs dans cette catégorie :")
                        for url in cat_urls[:10]:
                            st.text(url)
                        if len(cat_urls) > 10:
                            st.caption(f"... et {len(cat_urls) - 10} autres URLs")
                    
                    with col2:
                        # Actions
                        if st.button(f"🗑️ Supprimer", key=f"del_{cat_name}"):
                            del st.session_state.categories_custom[cat_name]
                            st.success(f"Catégorie '{cat_name}' supprimée")
                            st.rerun()
                        
                        # Renommer
                        new_name = st.text_input(
                            "Renommer",
                            value=cat_name,
                            key=f"rename_{cat_name}"
                        )
                        if new_name != cat_name and st.button(f"✏️ Valider", key=f"rename_btn_{cat_name}"):
                            st.session_state.categories_custom[new_name] = st.session_state.categories_custom[cat_name]
                            del st.session_state.categories_custom[cat_name]
                            st.success(f"Renommée en '{new_name}'")
                            st.rerun()
                    
                    # Ajouter/retirer des URLs manuellement
                    st.markdown("**Ajouter des URLs manuellement :**")
                    
                    # Filtre de recherche
                    search = st.text_input(
                        "Rechercher des URLs à ajouter",
                        placeholder="Tapez un mot-clé pour filtrer...",
                        key=f"search_{cat_name}"
                    )
                    
                    # URLs disponibles (pas encore dans cette catégorie)
                    available_urls = [u for u in st.session_state.all_urls if u not in cat_urls]
                    
                    if search:
                        available_urls = [u for u in available_urls if search.lower() in u.lower()]
                    
                    # Multiselect pour ajouter des URLs
                    if available_urls:
                        urls_to_add = st.multiselect(
                            f"Sélectionnez des URLs à ajouter ({len(available_urls)} disponibles)",
                            options=available_urls[:100],
                            key=f"add_urls_{cat_name}"
                        )
                        
                        if urls_to_add and st.button(f"➕ Ajouter {len(urls_to_add)} URL(s)", key=f"add_btn_{cat_name}"):
                            st.session_state.categories_custom[cat_name].extend(urls_to_add)
                            st.success(f"{len(urls_to_add)} URL(s) ajoutée(s) à '{cat_name}'")
                            st.rerun()
                    else:
                        st.info("Toutes les URLs sont déjà dans une catégorie ou aucune ne correspond à la recherche")
    
    with tab_create:
        st.markdown("### Créer une nouvelle catégorie")
        
        new_cat_name = st.text_input(
            "Nom de la catégorie",
            placeholder="Ex: Pantalons de travail"
        )
        
        # Méthode 1 : Par pattern
        st.markdown("**Méthode 1 : Ajouter par pattern**")
        pattern = st.text_input(
            "Pattern à rechercher dans les URLs",
            placeholder="Ex: /pantalon/ ou pantalon-de-travail",
            help="Toutes les URLs contenant ce texte seront ajoutées"
        )
        
        if pattern:
            matching_urls = [u for u in st.session_state.all_urls if pattern.lower() in u.lower()]
            st.info(f"✅ {len(matching_urls)} URLs correspondent au pattern '{pattern}'")
            
            if matching_urls:
                with st.expander("Voir les URLs correspondantes", expanded=False):
                    for url in matching_urls[:20]:
                        st.code(url, language=None)
                    if len(matching_urls) > 20:
                        st.caption(f"... et {len(matching_urls) - 20} autres")
        
        # Méthode 2 : Sélection manuelle
        st.markdown("**Méthode 2 : Sélection manuelle**")
        manual_urls = st.multiselect(
            "Sélectionnez des URLs",
            options=st.session_state.all_urls[:200],
            help="Vous pouvez sélectionner jusqu'à 200 URLs"
        )
        
        # Créer la catégorie
        if st.button("✨ Créer la catégorie", type="primary"):
            if not new_cat_name:
                st.error("❌ Veuillez donner un nom à la catégorie")
            elif new_cat_name in st.session_state.categories_custom:
                st.error(f"❌ La catégorie '{new_cat_name}' existe déjà")
            else:
                # Combiner pattern + manuel
                urls_to_add = []
                if pattern:
                    urls_to_add.extend([u for u in st.session_state.all_urls if pattern.lower() in u.lower()])
                if manual_urls:
                    urls_to_add.extend(manual_urls)
                
                # Dédupliquer
                urls_to_add = list(set(urls_to_add))
                
                if urls_to_add:
                    st.session_state.categories_custom[new_cat_name] = urls_to_add
                    st.success(f"✅ Catégorie '{new_cat_name}' créée avec {len(urls_to_add)} URLs")
                    st.rerun()
                else:
                    st.error("❌ Aucune URL sélectionnée")
    
    with tab_export:
        st.markdown("### Exporter/Importer vos catégories")
        
        # Export
        st.markdown("**📥 Exporter**")
        if st.session_state.categories_custom:
            export_data = {
                "categories": st.session_state.categories_custom,
                "total_urls": len(st.session_state.all_urls)
            }
            json_str = json.dumps(export_data, indent=2, ensure_ascii=False)
            
            st.download_button(
                label="💾 Télécharger les catégories (JSON)",
                data=json_str,
                file_name="categories_geo.json",
                mime="application/json"
            )
        else:
            st.info("Aucune catégorie à exporter")
        
        # Import
        st.markdown("**📤 Importer**")
        uploaded_file = st.file_uploader(
            "Importer un fichier de catégories",
            type=['json'],
            help="Fichier JSON exporté précédemment"
        )
        
        if uploaded_file:
            try:
                import_data = json.load(uploaded_file)
                if "categories" in import_data:
                    st.session_state.categories_custom = import_data["categories"]
                    st.success(f"✅ {len(import_data['categories'])} catégories importées")
                    st.rerun()
                else:
                    st.error("❌ Format de fichier invalide")
            except Exception as e:
                st.error(f"❌ Erreur lors de l'import : {str(e)}")

# ÉTAPE 3 : LANCER LE SCAN
if st.session_state.scan_done and st.session_state.categories_custom:
    st.markdown("---")
    st.markdown("## 🚀 Étape 3 : Lancer l'audit GEO")
    
    # Sélection des catégories à scanner
    selected_categories = st.multiselect(
        "Sélectionnez les catégories à auditer",
        options=list(st.session_state.categories_custom.keys()),
        default=list(st.session_state.categories_custom.keys())[:1] if st.session_state.categories_custom else [],
        help="Sélectionnez une ou plusieurs catégories pour l'audit"
    )
    
    # Limiter le nombre de pages
    max_pages = st.number_input(
        "Nombre max de pages à analyser par catégorie",
        min_value=1,
        max_value=200,
        value=50,
        help="Limiter le nombre de pages pour éviter de scanner tout le site"
    )
    
    if selected_categories:
        # Calculer le nombre total d'URLs
        total_urls = sum([len(st.session_state.categories_custom[cat]) for cat in selected_categories])
        urls_to_scan = min(total_urls, max_pages * len(selected_categories))
        
        st.info(f"📊 **{total_urls} URLs** dans les catégories sélectionnées → **{urls_to_scan} URLs** seront scannées (limité à {max_pages} par catégorie)")
        
        if st.button("🎯 Lancer l'audit GEO", type="primary"):
            st.success("🚀 Audit en cours de lancement...")
            st.info("⚠️ Fonctionnalité en cours de développement - L'audit GEO complet sera disponible dans la prochaine version")
            
            # TODO: Intégrer ici tout le code d'audit GEO existant
            # Pour l'instant, afficher juste les URLs qui seraient scannées
            with st.expander("URLs qui seront scannées", expanded=True):
                for cat in selected_categories:
                    st.markdown(f"### 📁 {cat}")
                    urls_cat = st.session_state.categories_custom[cat][:max_pages]
                    for url in urls_cat[:10]:
                        st.code(url, language=None)
                    if len(urls_cat) > 10:
                        st.caption(f"... et {len(urls_cat) - 10} autres URLs")
