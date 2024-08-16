import math
import pickle
import csv
import configparser
from collections import defaultdict, Counter
from tqdm import tqdm
from pywebio import start_server
from pywebio.input import select, file_upload
from pywebio.output import put_tabs, put_html, put_loading, put_markdown
from pywebio.session import set_env
import plotly.express as px
import pandas as pd

# Load configuration file
config = configparser.ConfigParser()
config.read('config.conf')

# Extract paths from the configuration
paths = config['data_paths']
server_settings = config['server_settings']

# Load necessary data files
with open(paths['kld_cache_with_titles'], "rb") as handle:
    pmid_to_concepts = pickle.load(handle)
print("Data loaded")

mrdef_lines = open(paths['mrdef_file'], encoding="utf-8", errors="ignore").readlines()

cui_to_explanation = defaultdict(
    str, {line.split("|")[0]: line.split("|")[5].strip() for line in mrdef_lines if line.split("|")[4] == "NCI"}
)

# Generate a global concept frequency counter from the pre-loaded data
global_concept_counter = Counter(
    cui for entry in pmid_to_concepts.values() for cui in set(entry['concepts'])
)

# Load definitions of concepts
concept_definitions = defaultdict(
    str, {line.split()[0].strip(): ' '.join(line.split()[1:]).strip() for line in open(paths['definitions_file'], encoding="utf-8", errors="ignore").readlines()}
)

# Load mapping from AUI (Atom Unique Identifier) to CUI (Concept Unique Identifier)
aui_to_cui = defaultdict(
    str, {row[7]: row[0] for row in csv.reader(open(paths['mrconso_file'], 'r', encoding='utf-8'), delimiter='|')}
)

# Load hierarchy information from MRHIER file
mrhier_lines = open(paths['mrhier_file'], encoding='utf-8', errors="ignore").readlines()

# Function to generate an HTML tree structure for the classic tree view
def generate_html_tree(node_id, nodes_map):
    """Recursively generates an HTML tree structure for the classic tree view."""
    node = nodes_map.get(node_id)
    if not node:
        return ""
    html = f'<li><span class="caret">{node["name"]}</span>'
    if node["children"]:
        html += '<ul class="nested">' + ''.join(generate_html_tree(child_id, nodes_map) for child_id in node["children"]) + '</ul>'
    return html + '</li>'

# Function to generate interactive sunburst and tree visualizations
def generate_visualizations(kl_divergence_list, target_terminology):
    """Generates interactive visualizations (sunburst and tree map) based on KL divergence scores."""
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
        if row[4] == target_terminology and row[0] in allowed_cuis:
            path = row[6].split('.') + [row[1]]
            parent_id = None
            for aui in path:
                if aui not in aui_to_node_id:
                    cui = aui_to_cui[aui]
                    aui_to_node_id[aui] = str(node_id_counter)
                    nodes.append({
                        "id": str(node_id_counter), 
                        "parent": parent_id, 
                        "name": concept_definitions[cui], 
                        "KLD": str(cui_to_kl_divergence[cui]), 
                        "Explanation": '<br>'.join([cui_to_explanation[cui][i:i+100] for i in range(0, len(cui_to_explanation[cui]), 100)])
                    })
                    parent_id = str(node_id_counter)
                    node_id_counter += 1
                else:
                    parent_id = aui_to_node_id[aui]

    # Convert node structure to DataFrame for Plotly visualizations
    df = pd.DataFrame(nodes)
    
    # Create tree map visualization
    fig_tree_map = px.treemap(
        df, names='name', parents='parent', ids='id', custom_data=["KLD", "Explanation"], maxdepth=5, color_discrete_sequence=px.colors.qualitative.D3
    )
    fig_tree_map.update_traces(hovertemplate='<b>%{label}</b><br>%{customdata[1]}')
    html_tree_map = fig_tree_map.to_html(include_plotlyjs="require", full_html=False, default_width="100%", default_height="1200")
    
    # Create sunburst chart visualization
    fig_sunburst = px.sunburst(
        df, names='name', parents='parent', ids='id', custom_data=["KLD", "Explanation"], maxdepth=5, color_discrete_sequence=px.colors.qualitative.D3
    )
    fig_sunburst.update_traces(hovertemplate='<b>%{label}</b><br>%{customdata[1]}')
    html_sunburst = fig_sunburst.to_html(include_plotlyjs="require", full_html=False, default_width="100%", default_height="1200")

    # Generate classic tree view HTML structure
    nodes_map = {node["id"]: {"name": node["name"], "children": []} for node in nodes}
    for node in nodes:
        if node["parent"]:
            nodes_map[node["parent"]]["children"].append(node["id"])

    html_tree = generate_html_tree(nodes[0]["id"], nodes_map)
    with open(paths['tree_view_template_output'], "w") as file:
        file.write(html_tree_template.replace("{html_tree}", html_tree))
    
    # Display visualizations in separate tabs
    put_tabs([
        {'title': 'Interactive Ontology Tree Map', 'content': put_html(html_tree_map)},
        {'title': 'Interactive Ontology Sunburst Chart', 'content': put_html(html_sunburst)},
        {'title': 'Classic Tree View', 'content': put_html(html_tree_template)}
    ])

# Main function for the terminology explorer
def terminology_explorer():
    """Main function to run the terminology explorer application."""
    set_env(title="Knowledge Graphs", output_max_width="100%")
    put_markdown("# Auto-Generated Knowledge Graphs for Conditions")
    
    # User selects a condition or uploads their own PMID file
    selected_condition = select(
        "Select condition or upload PMID file", 
        options=[
            "Melanoma - 128k PMIDs", "Dementia - 114k PMIDs", "Chronic Lymphocytic Leukemia - 22k PMIDs", 
            "Colorectal Cancer - 143k PMIDs", "Aneurysm - 158k PMIDs", "Diabetes (all) - 300k PMIDs", 
            "Upload your own PMIDs file"
        ]
    )
    
    # Load PMIDs based on user selection
    if selected_condition == "Upload your own PMIDs file":
        pmid_file_content = file_upload("Select large PMID list", accept=".txt")['content'].decode("utf-8").splitlines()
    else:
        pmid_file_content = open(paths['preset_folder'] + selected_condition + ".txt").read().splitlines()
    
    # Count the occurrence of each concept in the selected PMIDs
    user_concept_counter = Counter(
        cui for pmid in pmid_file_content if pmid in pmid_to_concepts for cui in set(pmid_to_concepts[pmid]['concepts'])
    )
    
    # Save the frequency of user concepts
    with open(paths['frequency_output'], "wb") as f:
        pickle.dump(user_concept_counter.most_common(), f)
    
    # User selects target terminology for visualization
    selected_terminology = select(
        "Select target terminology", 
        options=[
            "SNOMEDCT_US", "NCI", "MSH", "MSHGER", "LNC", "ATC", "ICD10", "ICD10CM", "ICD10PCS", "FMA"
        ]
    )
    
    # Calculate KL Divergence between global and user concept distributions
    with put_loading(shape='grow', color='info').style('margin-left:45%;'):
        global_probabilities = {cui: count / sum(global_concept_counter.values()) for cui, count in global_concept_counter.items()}
        user_probabilities = {cui: count / sum(user_concept_counter.values()) for cui, count in user_concept_counter.items()}
        
        kl_divergence_scores = {
            cui: p_user * (math.log(p_user / global_probabilities.get(cui, 1))) 
            for cui, p_user in user_probabilities.items() if p_user > 0 and global_probabilities.get(cui, 0) > 0
        }
        
        kl_divergence_sorted = sorted(kl_divergence_scores.items(), key=lambda x: -x[1])
        
        # Save the calculated KL divergence scores
        with open(paths['kld_cache_output'], 'wb') as fp:
            pickle.dump(kl_divergence_sorted, fp)
        
        # Generate visualizations
        generate_visualizations(kl_divergence_sorted, selected_terminology)

# Start the server for the web application
if __name__ == '__main__':
    start_server(terminology_explorer, ip=server_settings['ip'], port=int(server_settings['port']), debug=True)
