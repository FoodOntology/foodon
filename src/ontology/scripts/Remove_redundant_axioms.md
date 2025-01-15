# Cleanup of redundant axioms when new robot tables take place of hand-coded 
# axioms in manually curated files.  Run in /src/ontology/

BEWARE: inclusion of foodon_edit.ofn below BRINGS IN ITS IMPORTS TOO!!!!!

robot unmerge --input foodon-edit.ofn\
 --input imports/chebi_import.owl\
 --input imports/cob_import.owl\
 --input imports/general_import.owl\
 --input imports/ncbitaxon_import.owl\
 --input imports/ons_import.owl\
 --input imports/foodon_product_import.ofn\
 --input imports/robot_animals.ofn\
 --input imports/robot_dietary_supplement.ofn\
 --input imports/robot_food_process.ofn\
 --input imports/robot_meat_cuts.ofn\
 --input imports/robot_pasta.ofn\
 --input imports/robot_plant_parts.ofn\
 --input imports/robot_process.ofn\
 --input imports/robot_seafood.ofn\
 --input imports/robot_wine.ofn\
 --input imports/siren_augment_codes.ofn\
 --output foodon-fixed.ofn

 # BEWARE: inclusion of foodon_edit.ofn below includes ITS IMPORTS TOO, 
 # YEILDING ALMOST EMPTY FILE due to removal of source import items!!!!!

 robot unmerge --input imports/foodon_product_import.ofn\
 --input imports/chebi_import.owl\
 --input imports/cob_import.owl\
 --input imports/general_import.owl\
 --input imports/ncbitaxon_import.owl\
 --input imports/ons_import.owl\
 --input imports/robot_animals.ofn\
 --input imports/robot_dietary_supplement.ofn\
 --input imports/robot_food_process.ofn\
 --input imports/robot_meat_cuts.ofn\
 --input imports/robot_pasta.ofn\
 --input imports/robot_plant_parts.ofn\
 --input imports/robot_process.ofn\
 --input imports/robot_seafood.ofn\
 --input imports/robot_wine.ofn\
 --input imports/siren_augment_codes.ofn\
 --output imports/foodon_product_import.ofn

 robot unmerge --input imports/siren_augment_codes.ofn\
 --input imports/chebi_import.owl\
 --input imports/cob_import.owl\
 --input imports/general_import.owl\
 --input imports/ncbitaxon_import.owl\
 --input imports/ons_import.owl\
 --input imports/robot_animals.ofn\
 --input imports/robot_dietary_supplement.ofn\
 --input imports/robot_food_process.ofn\
 --input imports/robot_meat_cuts.ofn\
 --input imports/robot_pasta.ofn\
 --input imports/robot_plant_parts.ofn\
 --input imports/robot_process.ofn\
 --input imports/robot_seafood.ofn\
 --input imports/robot_wine.ofn\
 --output imports/siren_augment_codes.ofn
