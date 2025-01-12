---
layout: ontology_detail
id: foodon
title: FoodOn Food Ontology
jobs:
  - id: https://travis-ci.org/FoodOntology/foodon
    type: travis-ci
build:
  checkout: git clone https://github.com/FoodOntology/foodon.git
  system: git
  path: "."
contact:
  email: damion_dooley@sfu.ca
  label: Damion Dooley
  github: 
description: FoodOn Food Ontology is an ontology...
domain: stuff
homepage: https://github.com/FoodOntology/foodon
products:
  - id: foodon.owl
    name: "FoodOn Food Ontology main release in OWL format"
  - id: foodon.obo
    name: "FoodOn Food Ontology additional release in OBO format"
  - id: foodon.json
    name: "FoodOn Food Ontology additional release in OBOJSon format"
  - id: foodon/foodon-base.owl
    name: "FoodOn Food Ontology main release in OWL format"
  - id: foodon/foodon-base.obo
    name: "FoodOn Food Ontology additional release in OBO format"
  - id: foodon/foodon-base.json
    name: "FoodOn Food Ontology additional release in OBOJSon format"
dependencies:
- id: chebi
- id: cob

tracker: https://github.com/FoodOntology/foodon/issues
license:
  url: http://creativecommons.org/licenses/by/4.0/
  label: CC-BY
activity_status: active
---

FoodOn, the food ontology, contains vocabulary for naming food materials and their anatomical and taxonomic origins, from raw harvested food to processed food products, for humans and domesticated animals.

