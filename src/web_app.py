"""
BioKGrapher Web Application
===========================
A PyWebIO-based web application for generating and visualizing biomedical
knowledge graphs from PubMed literature using KL divergence ranking.
"""

import os
import sys
import math
import pickle
import csv
import configparser
from collections import defaultdict, Counter
from tqdm import tqdm
from pywebio import start_server
from pywebio.input import select, file_upload
from pywebio.output import put_tabs, put_html, put_loading, put_markdown, put_text
from pywebio.session import set_env
import plotly.express as px
import pandas as pd

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config():
    """Load and validate configuration file."""
    config = configparser.ConfigParser()
    config_path = os.path.join(SCRIPT_DIR, 'config.conf')

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    config.read(config_path)
    return config

def resolve_path(path):
    """Resolve a path relative to the script directory."""
    if os.path.isabs(path):
        return path
    return os.path.join(SCRIPT_DIR, path)

def validate_required_files(paths):
    """Validate that all required data files exist."""
    required_files = [
        ('kld_cache_with_titles', 'KLD cache file'),
        ('mrdef_file', 'MRDEF.RRF file'),
        ('definitions_file', 'Definitions file'),
        ('mrconso_file', 'MRCONSO.RRF file'),
        ('mrhier_file', 'MRHIER.RRF file'),
    ]

    missing_files = []
    for key, description in required_files:
        file_path = resolve_path(paths[key])
        if not os.path.exists(file_path):
            missing_files.append(f"  - {description}: {file_path}")

    if missing_files:
        error_msg = "Missing required data files:\n" + "\n".join(missing_files)
        error_msg += "\n\nPlease run the following to set up the required files:"
        error_msg += "\n  1. python index_pubmed.py  - to index PubMed and generate KLD cache"
        error_msg += "\n  2. python build_cache.py   - to build definitions and concept mappings"
        error_msg += "\n  3. Obtain UMLS files (MRCONSO.RRF, MRDEF.RRF, MRHIER.RRF) with a valid license"
        raise FileNotFoundError(error_msg)

# Load configuration
try:
    config = load_config()
    paths = config['data_paths']
    server_settings = config['server_settings']
except Exception as e:
    print(f"Error loading configuration: {e}")
    sys.exit(1)

# Validate required files before loading
try:
    validate_required_files(paths)
except FileNotFoundError as e:
    print(f"\n{e}")
    print("\nTo create sample data for testing, run: python setup.py --create-sample-data")
    sys.exit(1)

# Load the HTML tree template
template_path = resolve_path('template/tree_view_template.html')
if os.path.exists(template_path):
    with open(template_path, 'r', encoding='utf-8') as f:
        html_tree_template = f.read()
else:
    # Fallback basic template if file doesn't exist
    html_tree_template = """<!DOCTYPE html>
<html>
<head>
    <style>
        ul, #myUL { list-style-type: none; margin: 0; padding: 0; }
        .caret { cursor: pointer; user-select: none; }
        .caret::before { content: "\\25B6"; margin-right: 6px; }
        .caret-down::before { content: "\\25BC"; }
        .nested { display: none; }
        .active { display: block; }
    </style>
</head>
<body>
    <ul id="myUL">{html_tree}</ul>
    <script>
        var toggler = document.getElementsByClassName("caret");
        for (var i = 0; i < toggler.length; i++) {
            toggler[i].addEventListener("click", function() {
                this.parentElement.querySelector(".nested").classList.toggle("active");
                this.classList.toggle("caret-down");
            });
        }
    </script>
</body>
</html>"""

print("Loading data files...")

# Load KLD cache with PMID to concepts mapping
kld_cache_path = resolve_path(paths['kld_cache_with_titles'])
with open(kld_cache_path, "rb") as handle:
    pmid_to_concepts = pickle.load(handle)
print(f"  Loaded KLD cache: {len(pmid_to_concepts)} PMIDs")

# Load MRDEF file for concept explanations
mrdef_path = resolve_path(paths['mrdef_file'])
with open(mrdef_path, encoding="utf-8", errors="ignore") as f:
    mrdef_lines = f.readlines()

cui_to_explanation = defaultdict(str)
for line in mrdef_lines:
    parts = line.split("|")
    if len(parts) > 5 and len(parts) > 4 and parts[4] == "NCI":
        cui_to_explanation[parts[0]] = parts[5].strip()
print(f"  Loaded {len(cui_to_explanation)} concept explanations from MRDEF")

# Generate a global concept frequency counter from the pre-loaded data
global_concept_counter = Counter(
    cui for entry in pmid_to_concepts.values() for cui in set(entry['concepts'])
)
print(f"  Computed global concept frequencies: {len(global_concept_counter)} unique concepts")

# Load definitions of concepts
definitions_path = resolve_path(paths['definitions_file'])
concept_definitions = defaultdict(str)
with open(definitions_path, encoding="utf-8", errors="ignore") as f:
    for line in f:
        parts = line.split(maxsplit=1)
        if len(parts) >= 2:
            concept_definitions[parts[0].strip()] = parts[1].strip()
        elif len(parts) == 1:
            concept_definitions[parts[0].strip()] = ""
print(f"  Loaded {len(concept_definitions)} concept definitions")

# Load mapping from AUI (Atom Unique Identifier) to CUI (Concept Unique Identifier)
mrconso_path = resolve_path(paths['mrconso_file'])
aui_to_cui = defaultdict(str)
with open(mrconso_path, 'r', encoding='utf-8', errors='ignore') as f:
    reader = csv.reader(f, delimiter='|')
    for row in reader:
        if len(row) > 7:
            aui_to_cui[row[7]] = row[0]
print(f"  Loaded {len(aui_to_cui)} AUI to CUI mappings from MRCONSO")

# Load hierarchy information from MRHIER file
mrhier_path = resolve_path(paths['mrhier_file'])
with open(mrhier_path, encoding='utf-8', errors="ignore") as f:
    mrhier_lines = f.readlines()
print(f"  Loaded {len(mrhier_lines)} hierarchy entries from MRHIER")

print("Data loading complete!\n")


def generate_html_tree(node_id, nodes_map):
    """
    Recursively generates an HTML tree structure for the classic tree view.

    Args:
        node_id: The ID of the current node
        nodes_map: Dictionary mapping node IDs to node data

    Returns:
        HTML string representing the tree structure
    """
    node = nodes_map.get(node_id)
    if not node:
        return ""
    html = f'<li><span class="caret">{node["name"]}</span>'
    if node["children"]:
        html += '<ul class="nested">' + ''.join(
            generate_html_tree(child_id, nodes_map) for child_id in node["children"]
        ) + '</ul>'
    return html + '</li>'


def generate_visualizations(kl_divergence_list, target_terminology):
    """
    Generates interactive visualizations (sunburst and tree map) based on KL divergence scores.

    Args:
        kl_divergence_list: List of (CUI, KL divergence score) tuples, sorted by score
        target_terminology: The terminology to filter hierarchy by (e.g., 'SNOMEDCT_US', 'NCI')
    """
    nodes = []
    aui_to_node_id = {}
    node_id_counter = 0
    allowed_cuis = set()
    cui_to_kl_divergence = defaultdict(float)

    # Limit to top 2500 KL divergence scores
    for i, (cui, kl_divergence) in enumerate(kl_divergence_list):
        if i >= 2500:
            break
        cui_to_kl_divergence[cui] = kl_divergence
        allowed_cuis.add(cui)

    # Process the hierarchical data to build the node structure
    for line in mrhier_lines:
        row = line.split('|')
        if len(row) > 6 and row[4] == target_terminology and row[0] in allowed_cuis:
            path = row[6].split('.') + [row[1]]
            parent_id = None
            for aui in path:
                if aui not in aui_to_node_id:
                    cui = aui_to_cui[aui]
                    aui_to_node_id[aui] = str(node_id_counter)
                    explanation = cui_to_explanation[cui]
                    # Format explanation with line breaks for readability
                    formatted_explanation = '<br>'.join(
                        [explanation[i:i+100] for i in range(0, max(len(explanation), 1), 100)]
                    )
                    nodes.append({
                        "id": str(node_id_counter),
                        "parent": parent_id,
                        "name": concept_definitions[cui] if concept_definitions[cui] else cui,
                        "KLD": str(cui_to_kl_divergence[cui]),
                        "Explanation": formatted_explanation
                    })
                    parent_id = str(node_id_counter)
                    node_id_counter += 1
                else:
                    parent_id = aui_to_node_id[aui]

    # Check if we have any nodes to visualize
    if not nodes:
        put_markdown("## No Results Found")
        put_text(f"No concepts found matching terminology '{target_terminology}' in the hierarchy.")
        put_text("Try selecting a different terminology or providing a different PMID set.")
        return

    # Convert node structure to DataFrame for Plotly visualizations
    df = pd.DataFrame(nodes)

    # Create tree map visualization
    fig_tree_map = px.treemap(
        df, names='name', parents='parent', ids='id',
        custom_data=["KLD", "Explanation"],
        maxdepth=5,
        color_discrete_sequence=px.colors.qualitative.D3
    )
    fig_tree_map.update_traces(hovertemplate='<b>%{label}</b><br>%{customdata[1]}')
    html_tree_map = fig_tree_map.to_html(
        include_plotlyjs="require", full_html=False,
        default_width="100%", default_height="1200"
    )

    # Create sunburst chart visualization
    fig_sunburst = px.sunburst(
        df, names='name', parents='parent', ids='id',
        custom_data=["KLD", "Explanation"],
        maxdepth=5,
        color_discrete_sequence=px.colors.qualitative.D3
    )
    fig_sunburst.update_traces(hovertemplate='<b>%{label}</b><br>%{customdata[1]}')
    html_sunburst = fig_sunburst.to_html(
        include_plotlyjs="require", full_html=False,
        default_width="100%", default_height="1200"
    )

    # Generate classic tree view HTML structure
    nodes_map = {node["id"]: {"name": node["name"], "children": []} for node in nodes}
    for node in nodes:
        if node["parent"] and node["parent"] in nodes_map:
            nodes_map[node["parent"]]["children"].append(node["id"])

    # Generate HTML tree starting from root node
    html_tree = generate_html_tree(nodes[0]["id"], nodes_map)

    # Save the tree view HTML to file
    tree_output_path = resolve_path(paths['tree_view_template_output'])
    os.makedirs(os.path.dirname(tree_output_path), exist_ok=True)
    with open(tree_output_path, "w", encoding='utf-8') as file:
        file.write(html_tree_template.replace("{html_tree}", html_tree))

    # Create the filled template for display
    filled_template = html_tree_template.replace("{html_tree}", html_tree)

    # Display visualizations in separate tabs
    put_tabs([
        {'title': 'Interactive Ontology Tree Map', 'content': put_html(html_tree_map)},
        {'title': 'Interactive Ontology Sunburst Chart', 'content': put_html(html_sunburst)},
        {'title': 'Classic Tree View', 'content': put_html(filled_template)}
    ])


def get_available_presets():
    """Get list of available preset PMID files."""
    preset_folder = resolve_path(paths['preset_folder'])
    presets = []

    if os.path.exists(preset_folder):
        for filename in os.listdir(preset_folder):
            if filename.endswith('.txt'):
                # Remove .txt extension for display
                preset_name = filename[:-4]
                presets.append(preset_name)

    return sorted(presets)


def terminology_explorer():
    """Main function to run the terminology explorer application."""
    set_env(title="BioKGrapher - Knowledge Graph Explorer", output_max_width="100%")
    put_markdown("# BioKGrapher: Auto-Generated Knowledge Graphs")
    put_markdown("*Generate knowledge graphs from biomedical literature using KL divergence ranking*")

    # Get available presets
    available_presets = get_available_presets()

    # Build options list
    options = available_presets + ["Upload your own PMIDs file"]

    if not available_presets:
        put_markdown("**Note:** No preset PMID files found. You can upload your own file or add presets to the `presets/` folder.")
        options = ["Upload your own PMIDs file"]

    # User selects a condition or uploads their own PMID file
    selected_condition = select(
        "Select condition or upload PMID file",
        options=options
    )

    # Load PMIDs based on user selection
    if selected_condition == "Upload your own PMIDs file":
        uploaded = file_upload("Select PMID list file (one PMID per line)", accept=".txt")
        if uploaded is None:
            put_text("No file uploaded. Please refresh to try again.")
            return
        pmid_file_content = uploaded['content'].decode("utf-8").splitlines()
    else:
        preset_path = os.path.join(resolve_path(paths['preset_folder']), selected_condition + ".txt")
        if not os.path.exists(preset_path):
            put_text(f"Preset file not found: {preset_path}")
            return
        with open(preset_path, 'r', encoding='utf-8') as f:
            pmid_file_content = f.read().splitlines()

    # Filter valid PMIDs
    valid_pmids = [pmid.strip() for pmid in pmid_file_content if pmid.strip()]
    put_markdown(f"**Loaded {len(valid_pmids)} PMIDs**")

    # Count the occurrence of each concept in the selected PMIDs
    matched_pmids = 0
    user_concept_counter = Counter()
    for pmid in valid_pmids:
        if pmid in pmid_to_concepts:
            matched_pmids += 1
            for cui in set(pmid_to_concepts[pmid]['concepts']):
                user_concept_counter[cui] += 1

    put_markdown(f"*Found {matched_pmids} PMIDs in the index with {len(user_concept_counter)} unique concepts*")

    if matched_pmids == 0:
        put_markdown("**Error:** None of the provided PMIDs were found in the index.")
        put_text("Please ensure the PMIDs are valid and the index has been built properly.")
        return

    # Save the frequency of user concepts
    frequency_output_path = resolve_path(paths['frequency_output'])
    with open(frequency_output_path, "wb") as f:
        pickle.dump(user_concept_counter.most_common(), f)

    # User selects target terminology for visualization
    selected_terminology = select(
        "Select target terminology for knowledge graph",
        options=[
            "SNOMEDCT_US", "NCI", "MSH", "MSHGER", "LNC",
            "ATC", "ICD10", "ICD10CM", "ICD10PCS", "FMA"
        ]
    )

    # Calculate KL Divergence between global and user concept distributions
    put_markdown("**Calculating KL Divergence and generating visualizations...**")
    with put_loading(shape='grow', color='info').style('margin-left:45%;'):
        total_global = sum(global_concept_counter.values())
        total_user = sum(user_concept_counter.values())

        global_probabilities = {
            cui: count / total_global
            for cui, count in global_concept_counter.items()
        }
        user_probabilities = {
            cui: count / total_user
            for cui, count in user_concept_counter.items()
        }

        kl_divergence_scores = {}
        for cui, p_user in user_probabilities.items():
            p_global = global_probabilities.get(cui, 0)
            if p_user > 0 and p_global > 0:
                kl_divergence_scores[cui] = p_user * math.log(p_user / p_global)

        kl_divergence_sorted = sorted(kl_divergence_scores.items(), key=lambda x: -x[1])

        # Save the calculated KL divergence scores
        kld_output_path = resolve_path(paths['kld_cache_output'])
        with open(kld_output_path, 'wb') as fp:
            pickle.dump(kl_divergence_sorted, fp)

        # Generate visualizations
        generate_visualizations(kl_divergence_sorted, selected_terminology)


def main():
    """Entry point for the application."""
    print("=" * 60)
    print("BioKGrapher - Knowledge Graph Explorer")
    print("=" * 60)
    print(f"Starting server on {server_settings['ip']}:{server_settings['port']}")
    print("Open your browser and navigate to the URL shown below.")
    print("=" * 60)

    start_server(
        terminology_explorer,
        port=int(server_settings['port']),
        host=server_settings['ip'],
        debug=True
    )


if __name__ == '__main__':
    main()
