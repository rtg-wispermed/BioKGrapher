# BioKGrapher
### repo is in progress
## Overview
BioKGrapher is a comprehensive tool designed for the automatic construction of knowledge graphs (KGs) from large-scale biomedical literature, processing PubMed IDs as input. By leveraging NLP techniques, BioKGrapher extracts and ranks biomedical concepts, integrating them into structured KGs. This tool can be valuable to construct specialized KGs to get a conceptual view on a topic of interest or to export the KG for further applications such as predictive modeling, drug repurposing, document classification, RAG and decision support systems.
## Demo
![](https://github.com/rtg-wispermed/BioKGrapher/blob/main/demo.gif)
## Key Features
- Automatic Knowledge Graph Construction: Extracts and integrates biomedical concepts from large PMID sets
- Named Entity Recognition and Linking (NER+NEL): Utilizes [MedCAT](https://github.com/CogStack/MedCAT) for identifying and normalizing biomedical concepts using the UMLS Metathesaurus
- Concept Weighting and Re-Ranking: Applies Kullback-Leibler divergence and local frequency weighting to identify prevalent concepts specific to the provided set
- Hierarchical Structuring and Relationship Mapping: Constructs hierarchical knowledge graphs with semantic triples using UMLS's MRHIER and MRREL files
- Evaluation: Evaluates constructed KGs by comparing them with concepts extracted from evidence-based clinical practice guidelines.
- Downstream Applications: Demonstrates utility in document classification and an example drug repurposing tasks.

## Projet Setup
Clone the Repository:
```bash
git clone https://github.com/rtg-wispermed/BioKGrapher.git
```
Navigate to the Project
```bash
cd BioKGrapher
```
Install requirements
```bash
pip install -r requirements.txt
```

## UMLS License Requirement:
BioKGrapher requires a valid UMLS license to access and use the UMLS Metathesaurus files. Obtain a license from the [UMLS Terminology Services](https://www.nlm.nih.gov/databases/umls.html).

## Prerequisites
### Download public MedCAT Model
Once you have obtained a license, sign into your NIH profile / UMLS license and [download one of the following public MedCAT models](https://uts.nlm.nih.gov/uts/login?service=https://medcat.rosalind.kcl.ac.uk/auth-callback): 
- UMLS Full. >4MM concepts trained self-supervsied on MIMIC-III **was used in this work**
- SNOMED International (Full SNOMED modelpack trained on MIMIC-III)

Unzip the model into the empty models folder.

### Download required UMLS files
[Download the Full UMLS Release Files](https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html) and replace the following UMLS placeholder files with the ones from your UMLS Rlease Files:
- MRCONSO.RRF
- MRHIER.RRF
- MRREL.RRF
- MRDEF.RRF

It is recommended to stick to a UMLS Rlease that is the same version or newer to the one that was used in the MedCAT model, eg. UMLS Release **2022AA** and newer.

## Building the Index
Navigate to the index/baseline folder
```bash
cd index/baseline
```

Download the PubMed baseline files:
```bash
wget -nc ftp://ftp.ncbi.nlm.nih.gov/pubmed/baseline/*.xml.gz
```
and also (optionally) add the latest Updatefiles for the latest publications:
```bash
wget -nc ftp://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/*.xml.gz
```
