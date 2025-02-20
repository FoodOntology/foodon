This folder contains the build files for creating the FoodOn ontology, including files like foodon.owl which end up in the root folder of this github repository.

##To view

Briefly, the main file to look at is foodon.owl (in Protege or another OWL ontology editor); 
it should import the ncbitaxon_import.owl and other imports/ files too.  Use Stanford's free [Protege](http://protege.stanford.edu) ontology editor to load the main file, and browse the Langual hierarchy under the "Classes" tab.  It should also be viewable on https://www.ebi.ac.uk/ols/ soon too.

##To edit

The /src/ontology/foodon-edit.ofn can be edited using Protege; other /src/ontology/imports/ files are generated from scripts (either robot or ontofox related).  The COB, NCBITaxon, and general_ontofox.txt specification files contain term identifiers to import from respective ontologies.  

## To TEST

robot reason --input foodon-edit.owl --reasoner hermit --equivalent-classes-allowed none

##To regenerate the files

foodon.owl is generated by the 

> **make**

or 

> **sh run.sh make**

command when one is in the /src/ontology/ folder.  It determines if the foodon-edit.ofn or any imports/ folder files have changed and if so reruns the update/import process on them, and will generate a report of curation issues in **reports/foodon-base.owl-obo-report.tsv** with the following kinds of error:

	ERROR: need to be addressed before publishing
	WARN: items which need improvement.
	INFO: cosmetic edits perhaps.

Note: if the above report isn't generated, this means one must refresh or "touch" the date on the **foodon-edit.ofn** file to trigger the report to be generated.

To update a particular import file (rather than running make), type:

> sh run.sh make refresh-[name of ontology or ontofox import collection]

Further instructions are at: https://oboacademy.github.io/obook/howto/update-import/

##To run SPARQL queries

Here is an example of launching a sparql query via command line directly using the **robot** http://robot.obolibrary.org/query.html query command. The end result is a list of FoodOn term labels and synonyms.

robot query --input foodon-full.owl --query foodon_synonyms.sparql test.tsv --format TSV

## Other tools: ontofetch.py

To run ontofetch.py on local foodon-merged.owl and get a json or tsv output of all foodon terms and their ids, synonyms and parents:

python ../../../ontofetch/ontofetch.py foodon-full.owl -r http://purl.obolibrary.org/obo/BFO_0000001 -o test/

Ontofetch is available at: https://github.com/cidgoh/ontofetch .  It requires python 3, and the python rdflib module.
