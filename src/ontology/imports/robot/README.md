To regenerate an owl file from a robot template, run this style of command:
NOTE: --input command ONLY ALLOWS ONE INPUT file; if you do multiple --input
only LAST one is used.


FoodOn ontology robot managed term table imports:
animals.tsv
dietary_supplement.tsv
pasta.tsv
plant_parts.tsv
process.tsv
seafood.tsv
wine.tsv


robot template --template animals.tsv\
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_animals.ofn" \
  --output ../robot_animals.ofn

robot template --template dietary_supplement.tsv\
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_dietary_supplement.ofn" \
  --output ../robot_dietary_supplement.ofn

robot template --template pasta.tsv \
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_pasta.ofn" \
  --output ../robot_pasta.ofn

robot template --template plant_parts.tsv \
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_plant_parts.ofn" \
  --output ../robot_plant_parts.ofn

robot template --template process.tsv\
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_process.ofn" \
  --output ../robot_process.ofn

robot template --template seafood.tsv\
  --input "../../foodon-merged.ofn" \
  --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/robot_seafood.ofn" \
  --output ../robot_seafood.ofn

robot template --template wine.tsv \
  --input "../../foodon-merged.ofn" \
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

grep -v '^Declaration' imports/robot_plant_parts.ofn > temp.ofn
robot unmerge --input foodon-edit.ofn --input temp.ofn --output foodon-edit.ofn
robot unmerge --input imports/foodon_product_import.ofn --input temp.ofn --output imports/foodon_product_import.ofn
rm temp.ofn

