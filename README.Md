# FOODON
[![Build Status](https://travis-ci.org/FoodOntology/foodon.svg?branch=master)](https://travis-ci.org/FoodOntology/foodon)

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](http://creativecommons.org/licenses/by/4.0/)

Our [FoodOn.org](https://foodon.org) website introduces FoodOn and its design and community.

FoodOn aims to be the Lingua Franca about human and companion animal food product vocabulary. As an open-source inter-agency supported project, it provides a neutral and ontology-driven standard for government agencies, industry, nonprofits and consumers to name and reference food products and their components throughout the food supply chain. In partnership with other ontologies, the project also explores the semantic bedrock of vocabulary involved in modelling food composition, food processing, nutrition and food related disease.

FoodOn was initially based on a conversion of the [LanguaL.org](http://langual.org) food indexing thesaurus, integrated with OBOFoundry ontology terms from UBERON, ChEBI, and NCBITaxon and others.

Contact Us

* Join our [mailing list](https://groups.google.com/forum/#!forum/foodon-consortium/join)
* Online discussion about FoodOn terms is at the [issue tracker](https://github.com/FoodOntology/foodon/issues).
* Email us at info[at]genepio.org

## To view

An easy place to browse FoodOn is at [https://www.ebi.ac.uk/ols/ontologies/foodon](https://www.ebi.ac.uk/ols/ontologies/foodon). As well the URI's of terms in the ontology resolve to the comprehensive [Ontobee ontology lookup service](http://www.ontobee.org/). It is organized according to the upper level BFO ontology, so most terms can be browsed by starting at the BFO "entity" term (e.g. in [Ontobee](http://www.ontobee.org/ontology/FOODON?iri=http://purl.obolibrary.org/obo/BFO_0000001)).

The foodon_core.owl main file currently contains everything FoodOn has to offer, including a "foodon food product" branch of over 9,000 products. foodon_siren.owl has a slightly larger food product database, but is kept only for reference, as we improve the foodon food product branch. The food products mainly originate from the FDA SIREN indexed food database, imported from LanguaL.org. (Currently foodon.owl is identical to foodon_core.owl).

You can look at either file using in Protege or another OWL ontology editor.  It should import the ncbitaxon_import.owl and other imports/ files too.  After cloning this repository, use the [Protege](http://protege.stanford.edu) ontology editor to load the main file, and browse the Langual hierarchy under the "Classes" tab.  
