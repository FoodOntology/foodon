#!/bin/bash

# Author: Anoosha Sehar https://orcid.org/0000-0001-5275-8866
# Author: Kai Blumberg https://orcid.org/0000-0002-3410-4655

# Example run in this folder: sh run_organism_parts.sh MODE=test MODULE=animal TERM_CONTRIBUTOR_STR=<orcid_1>
# Parameters:
#   MODE=test|run
#   MODULE=animal|plant
#   TERM_CONTRIBUTOR_STR=<orcid_1>|<orcid_2>| etc.
#   TAG_NEW=true   (PLANT-ONLY, TEST MODE ONLY) - tags every brand-new term
#                  with a removable "demo/test term" note so it's easy to
#                  spot in Protege. Never applies to a real (MODE=run) compile.
#
# PLANT-ONLY: every run also pulls plant_parts.tsv, plants.tsv, and menus.tsv
# straight from their live Google Sheet tabs first, so you're always
# compiling against today's version of each - no manual export/upload
# needed. Read-only: this only ever downloads FROM the sheet, there is
# nothing here that writes to it.
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

# PLANT-ONLY: plant compiles through a menu-driven mechanism, which needs
# one extra input organism_parts.py's other modules don't use - menus.tsv
# (-mu), mapping each checked picklist item to its plant_parts.tsv row.
# Empty string for every other module, so animal/seafood run exactly as before.
if [ "$MODULE" = 'plant' ]; then
   PLANT_EXTRA="-mu $RESOURCES_DIR/menus.tsv"
else
   PLANT_EXTRA=''
fi

# PLANT-ONLY, REAL RUN ONLY: local-only log of the date each species was first
# compiled (xsd:dateTime, UTC - same format as the 'created' column). Never
# fetched from or written to Google Sheets, and deliberately left OUT of test
# mode below (same reason id_mapping_table.tsv is never updated by a test run
# either - test runs are meant to be disposable and repeatable).
if [ "$MODULE" = 'plant' ]; then
   SPECIES_ADDED_EXTRA="-sa $RESOURCES_DIR/species_added_log.tsv"
else
   SPECIES_ADDED_EXTRA=''
fi

# PLANT-ONLY, REAL RUN ONLY: running history of every real compile - date, how
# many species, how many terms (new + reused). Meant to be committed, unlike
# the two logs above - this is the one that answers "how many terms has the
# plant module generated in total over time."
if [ "$MODULE" = 'plant' ]; then
   COMPILE_HISTORY_EXTRA="-ch $RESOURCES_DIR/plant_compile_history.tsv"
else
   COMPILE_HISTORY_EXTRA=''
fi

# Species input filename: plant's is named plants.tsv (matches the module
# name, easier to recognize at a glance). animal/seafood/fungus keep their
# existing species_list.tsv - unchanged, so nothing else breaks.
if [ "$MODULE" = 'plant' ]; then
   SPECIES_FILE=plants.tsv
else
   SPECIES_FILE=species_list.tsv
fi

# PLANT-ONLY: pull plant_parts.tsv, plants.tsv, and menus.tsv from their live
# Google Sheet tabs before doing anything else. Each one only ever overwrites
# its local file if what comes back actually looks like that file's real
# table (starts with its real header row) - if the sheet ever requires
# sign-in or something else goes wrong, it warns and just keeps using
# whatever is already there, rather than either corrupting it or blocking
# the whole compile.
if [ "$MODULE" = 'plant' ]; then
   SHEET_ID="1zaF8gcZaU6_qejmh6NXPQoPXsi8vwmHCzCAK29VyDRM"

   fetch_sheet_tab () {
      LABEL="$1"; GID="$2"; DEST="$3"; HEADER_PATTERN="$4"
      TMP_SHEET_FETCH="/tmp/sheet_fetch_$$.tsv"

      echo "Fetching the live '$LABEL' tab..."
      curl -sL "https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=tsv&gid=${GID}" -o "$TMP_SHEET_FETCH" || true

      FIRST_LINE=$(head -1 "$TMP_SHEET_FETCH" 2>/dev/null || echo '')
      if [[ "$FIRST_LINE" == $HEADER_PATTERN ]]; then
         cp "$TMP_SHEET_FETCH" "$DEST"
         echo "$(basename "$DEST") updated from the live sheet ($(wc -l < "$DEST") lines)."
      else
         echo "WARNING: the download didn't look like the $LABEL table (possibly a"
         echo "Google sign-in/error page, or a network issue). Continuing with whatever"
         echo "$(basename "$DEST") is already there - it was NOT touched."
      fi
      rm -f "$TMP_SHEET_FETCH"
   }

   fetch_sheet_tab "plant parts" "204973855" "$MODULE_DIR/plant_parts.tsv" 'DISABLED*ID*parent class*'
   fetch_sheet_tab "plants" "1963371815" "$RESOURCES_DIR/plants.tsv" 'Compiled*anatomical_parent_id*organism_base*'
   fetch_sheet_tab "menus" "459684437" "$RESOURCES_DIR/menus.tsv" 'name*title*meaning*ALL*'
fi


if [ $MODE = 'test' ]; then

   # TEST MODE ONLY: optionally tag brand-new terms for easy demo review/removal.
   # Never wired into the MODE=run block below, so a real compile can't get tagged.
   if [ "$TAG_NEW" = 'true' ]; then TAG_EXTRA='-tn'; else TAG_EXTRA=''; fi

   TEST_DIR=$MODULE_DIR/TEST_RUN_RESULTS
   if [ ! -d "$TEST_DIR" ] ; then mkdir "$TEST_DIR" ; fi
   cp $RESOURCES_DIR/id_integer.txt $TEST_DIR/id_integer.txt
   python3 organism_parts.py -t $MODULE_DIR/${MODULE}_parts.tsv -i $RESOURCES_DIR/$SPECIES_FILE -o $TEST_DIR/test_${MODULE}_template.tsv -m $RESOURCES_DIR/id_mapping_table.tsv -n $TEST_DIR/mapping_table_output.tsv -org $MODULE -id $TEST_DIR/id_integer.txt $TC $PLANT_EXTRA $TAG_EXTRA
   robot template --template $TEST_DIR/test_${MODULE}_template.tsv -i ../foodon-full.owl --ontology-iri "http://purl.obolibrary.org/test_${MODULE}_template.owl" -o $TEST_DIR/test_${MODULE}_module.ofn
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
            python3 organism_parts.py -t $MODULE_DIR/${MODULE}_parts.tsv -i $RESOURCES_DIR/$SPECIES_FILE -o $INTERMEDIATE_DIR/${MODULE}_template.tsv -m $RESOURCES_DIR/id_mapping_table.tsv -n $INTERMEDIATE_DIR/mapping_table_output.tsv -org $MODULE -id $RESOURCES_DIR/id_integer.txt $TC $PLANT_EXTRA $SPECIES_ADDED_EXTRA $COMPILE_HISTORY_EXTRA
            echo "Success running organism_parts.py with $MODULE module!"
            robot template --template $INTERMEDIATE_DIR/${MODULE}_template.tsv -i ../foodon-full.owl --ontology-iri "http://purl.obolibrary.org/obo/foodon/imports/${MODULE}/${MODULE}_parts.owl" -o $MODULE_DIR/${MODULE}_parts.ofn
            echo "Success running generated $MODULE robot template!"

            if [ -f $INTERMEDIATE_DIR/mapping_table_output.tsv ] ; then cat $INTERMEDIATE_DIR/mapping_table_output.tsv >> $RESOURCES_DIR/id_mapping_table.tsv ; fi
            rm -r $INTERMEDIATE_DIR

            echo "You may now commit your changes to the following files:"
            echo "'$MODULE_DIR/${MODULE}_parts.tsv'"
            echo "'$MODULE_DIR/${MODULE}_parts.ofn'"
            echo "'$RESOURCES_DIR/id_integer.txt'"
            echo "'$RESOURCES_DIR/id_mapping_table.tsv'"
            if [ -f "$RESOURCES_DIR/menus.tsv" ]; then echo "'$RESOURCES_DIR/menus.tsv' (also fetched fresh from the live sheet every run, same as plants.tsv, so commit it too if it changed)"; fi
            if [ "$MODULE" = 'plant' ]; then echo "'$RESOURCES_DIR/plant_compile_history.tsv' (running history of species/term counts - worth committing too, so it builds up over time)"; fi
            if [ -f "$RESOURCES_DIR/species_added_log.tsv" ]; then echo "'$RESOURCES_DIR/species_added_log.tsv' (records which contributor first added each species and when - worth committing too, so it builds up over time)"; fi
            echo "As well as any changes you might have have made to the 'id_mapping_table.tsv' and or '$SPECIES_FILE' file(s) in '$RESOURCES_DIR'."
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
