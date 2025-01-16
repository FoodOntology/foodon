To regenerate an owl file from a robot template, run this style of command:
NOTE: --input command ONLY ALLOWS ONE INPUT file; if you do multiple --input
only LAST one is used.

To do a once-off generation of tsv from existing isolated .owl file of food items or processes, run the following. (Used to fetch content for google sheet via a manually exported .owl file hierarchy of food processes originating in foodon-edit.owl.
robot export --input ../robot_process_import.owl   --header "ID|LABEL|SubClass Of [NAME NAMED]|SubClass Of [NAME ANON]|Equivalent Class|oboInOwl:hasSynonym|oboInOwl:hasExactSynonym|oboInOwl:hasNarrowSynonym|oboInOwl:hasDbXref|IAO:0000115|IAO:0000600|rdfs:comment|IAO:0000117|IAO:0000114|dc:date"   --sort "ID"   --format tsv   --export robot_process_import.tsv


FoodOn ontology robot managed term table imports:
animals.tsv
dietary_supplement.tsv
pasta.tsv
plant_parts.tsv
process.tsv
seafood.tsv
wine.tsv


robot template --template animals.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_animals.ofn" \
  --output ../robot_animals.ofn

robot template --template meat_cuts.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_meat_cuts.ofn" \
  --output ../robot_meat_cuts.ofn

robot template --template animal_parts.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/animal_parts.ofn" \
  --output ../robot_animal_parts.ofn

robot template --template dietary_supplement.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_dietary_supplement.ofn" \
  --output ../robot_dietary_supplement.ofn

robot template --template pasta.tsv \
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_pasta.ofn" \
  --output ../robot_pasta.ofn

robot template --template plant_parts.tsv \
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_plant_parts.ofn" \
  --output ../robot_plant_parts.ofn

robot template --template process.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_process.ofn" \
  --output ../robot_process.ofn

robot template --template food_process.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_food_process.ofn" \
  --output ../robot_food_process.ofn

robot template --template seafood.tsv\
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_seafood.ofn" \
  --output ../robot_seafood.ofn

robot template --template wine.tsv \
  --prefix "dcterms:http://purl.org/dc/terms/" \
  --input "../../foodon-full.owl" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_wine.ofn" \
  --output ../robot_wine.ofn




An experimental USDA FDC robot managed ontology: fdc.tsv

robot template --template fdc.tsv \
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_fdc.owl" \
  --output ../robot_fdc.owl


The --input "../../foodon-merged.ofn" parameter brings in entities that are referenced in axioms.
The --prefix parameter is used to expand abbreviated namespace URLs.
All output files get delivered to parent directory.  Manually import them in FoodOn (in Active Ontology -> Ontology Imports section of Protege.

Do this against foodon-edit.owl and all imports/foodon_product_import.ofn etc.    
