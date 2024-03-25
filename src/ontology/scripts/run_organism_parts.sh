#!/bin/bash

# Author: Kai Blumberg https://orcid.org/0000-0002-3410-4655

# Example run in this folder: sh run_organism_parts.sh MODE=test MODULE=animal
# Parameters:
#   MODE=test|run
#   MODULE=animal|plant
#   TERM_CONTRIBUTOR_STR=https://orcid.org/0000-0001-5275-8866|contributor2| etc.
#
set -e

for ARGUMENT in "$@"
do
   KEY=$(echo $ARGUMENT | cut -f1 -d=)

   KEY_LENGTH=${#KEY}
   VALUE="${ARGUMENT:$KEY_LENGTH+1}"

   export "$KEY"="$VALUE"
done


MODULE_DIR=../imports/$MODULE
RESOURCES_DIR=$MODULE_DIR/resources


if [ $TERM_CONTRIBUTOR_STR ]; then TC="-c $TERM_CONTRIBUTOR_STR"; else TC=''; fi


if [ $MODE = 'test' ]; then
   
   TEST_DIR=$MODULE_DIR/TEST_RUN_RESULTS
   if [ ! -d "$TEST_DIR" ] ; then mkdir "$TEST_DIR" ; fi
   cp $RESOURCES_DIR/id_integer.txt $TEST_DIR/id_integer.txt
   python3 organism_parts.py -t $MODULE_DIR/${MODULE}_parts.tsv -i $RESOURCES_DIR/species_list.tsv -o $TEST_DIR/test_${MODULE}_template.tsv -m $RESOURCES_DIR/id_mapping_table.tsv -n $TEST_DIR/mapping_table_output.tsv -org $MODULE -id $TEST_DIR/id_integer.txt $TC
   robot template --template $TEST_DIR/test_${MODULE}_template.tsv -i ../foodon-merged.ofn --ontology-iri "http://purl.obolibrary.org/test_${MODULE}_template.owl" -o $TEST_DIR/test_${MODULE}_module.ofn
   echo ""
   echo "Test $MODULE module run successful!"
   echo "Check the files in '$TEST_DIR' to make sure everything is as expected."
   echo "See '$TEST_DIR/mapping_table_output.tsv' file summarizing the new terms that will be added (once fully run)."
   echo "It is safe to run the $MODULE module in test mode as many times as needed."
   echo "When ready run the full version of the $MODULE module."
   echo "Please do not commit the files in '$TEST_DIR' directory, they can be deleted once you are ready to move on to running the full module."


elif [ $MODE = 'run' ]; then
   while true; do
    read -p "Have you already run the script in test mode?" yesno
    case $yesno in
        [Yy]* ) 
            echo ""
            echo "Preliminary test confirmed, running $MODULE module!"
            # run steps here e.g. echo "run case."
            INTERMEDIATE_DIR=$MODULE_DIR/intermediate
            if [ ! -d "$INTERMEDIATE_DIR" ] ; then mkdir "$INTERMEDIATE_DIR" ; fi
            python3 organism_parts.py -t $MODULE_DIR/${MODULE}_parts.tsv -i $RESOURCES_DIR/species_list.tsv -o $INTERMEDIATE_DIR/${MODULE}_template.tsv -m $RESOURCES_DIR/id_mapping_table.tsv -n $INTERMEDIATE_DIR/mapping_table_output.tsv -org $MODULE -id $RESOURCES_DIR/id_integer.txt $TC
            echo "Success running organism_parts.py with $MODULE module!"
            robot template --template $INTERMEDIATE_DIR/${MODULE}_template.tsv -i ../foodon-merged.ofn --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/${MODULE}/${MODULE}_parts.owl" -o $MODULE_DIR/${MODULE}_parts.ofn
            echo "Success running generated $MODULE robot template!"
            
            if [ -f $INTERMEDIATE_DIR/mapping_table_output.tsv ] ; then cat $INTERMEDIATE_DIR/mapping_table_output.tsv >> $RESOURCES_DIR/id_mapping_table.tsv ; fi
            rm -r $INTERMEDIATE_DIR

            echo "You may now commit your changes to the following files:"
            echo "'$MODULE_DIR/${MODULE}_parts.tsv'"
            echo "'$MODULE_DIR/${MODULE}_parts.ofn'"
            echo "'$RESOURCES_DIR/id_integer.txt'"
            echo "'$RESOURCES_DIR/id_mapping_table.tsv'"
            echo "As well as any changes you might have have made to the 'id_mapping_table.tsv' and or 'species_list.tsv' file(s) in '$RESOURCES_DIR'."
            exit

        ;;
        [Nn]* ) 
            echo "You answered no, please make sure to run the script in test mode first."
            exit
        ;;
        * ) echo "Please answer either yes or no.";;
    esac
   done


else
   echo "MODE must be 'run' or 'test'."
fi
