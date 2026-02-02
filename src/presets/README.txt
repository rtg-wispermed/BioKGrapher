BioKGrapher Presets Folder
==========================

This folder contains preset PMID files for common conditions.
Each file should contain one PubMed ID (PMID) per line.

How to Create a Preset File:
1. Search for your condition on PubMed (https://pubmed.ncbi.nlm.nih.gov/)
2. Use the "Save" feature to download PMIDs as a text file
3. Save the file in this folder with the format: "Condition Name.txt"
4. The filename (without .txt) will appear in the web application dropdown

Example Files to Create:
- Melanoma - 128k PMIDs.txt
- Dementia - 114k PMIDs.txt
- Chronic Lymphocytic Leukemia - 22k PMIDs.txt
- Colorectal Cancer - 143k PMIDs.txt
- Aneurysm - 158k PMIDs.txt
- Diabetes (all) - 300k PMIDs.txt

Getting PMIDs from PubMed:
1. Go to https://pubmed.ncbi.nlm.nih.gov/
2. Enter your search query (e.g., "melanoma")
3. Click "Save" and select "All results"
4. Choose Format: PMID
5. Click "Create file" and save

Programmatic Access:
You can also use the NCBI E-utilities to programmatically fetch PMIDs:
https://www.ncbi.nlm.nih.gov/books/NBK25499/

Example using E-utilities:
esearch -db pubmed -query "melanoma" | efetch -format uid > "Melanoma.txt"
