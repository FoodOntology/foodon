#!/bin/sh
OBO=http://purl.obolibrary.org/obo
ONT=foodon
BASE=$(OBO)/$(ONT)
SRC=$(ONT)-edit.owl
RELEASEDIR=../..
ROBOT= robot
OWLTOOLS= owltools

# THE "make" PROCESS VIA robot/owlapi PAYS ATTENTION TO THE catalog-v001.xml
# file residing in this directory.  If you are running into the problem of
# local files not being found, e.g. with this error coming up:
#
#   org.semanticweb.owlapi.model.UnloadableImportException: Could not load \
#   imported ontology: <http://purl.obolibrary.org/obo/FOODON/imports/efo_import.owl> \
#   Cause: https://raw.githubusercontent.com/FoodOntology/foodon/master/src/imports/efo_import.owl

# then the catalog catalog-v001.xml file likely needs to be edited, or delete it
# and load the root [ontology]-edit.owl file in Protege and ensure the
# import file connections are established correctly.  This will rewrite the
# catalog-v001.xml file, and the make process should succeed.

# Temporarily foodon.owl and foodon_core.owl are the same.

all: all_imports $(ONT).owl

.PHONY: test
test: reason

# Run a basic reasoner to find inconsistencies
.PHONY: reason
reason: foodon-merged.owl
	$(ROBOT) reason --input $< --reasoner ELK --equivalent-classes-allowed none

prepare_release: all
	cp $(ONT).owl $(RELEASEDIR) &&\
	cp $(ONT).owl $(ONT)_core.owl &&\
	cp $(ONT)_core.owl $(RELEASEDIR) &&\
	cp $(ONT)_siren.owl $(RELEASEDIR) &&\
	cp foodon_synonyms.tsv $(RELEASEDIR) &&\
	cp imports/*.owl $(RELEASEDIR)/imports/ &&\
	git add $(RELEASEDIR)/imports/*.owl &&\
	(test -f subsets/*.owl && cp subsets/*.owl $(RELEASEDIR)/subsets && git add $(RELEASEDIR)/subsets/* || echo "no subsets") &&\
	echo "Release files are now in $(RELEASEDIR) - now you should commit, push and make a release on github"

#make
#Annotates foodon.owl ( $(ONT).owl ) file with release
$(ONT).owl: $(SRC)
	$(ROBOT) reduce -i $< -r ELK annotate --xml-entities -V $(BASE)/releases/`date +%Y-%m-%d`/$(ONT).owl --typed-annotation http://purl.org/dc/terms/date `date +%Y-%m-%d` xsd:date -o "$@"

# Merge ontology and imports into one file, and commment out imports tags
	$(ROBOT) merge --input $(ONT).owl --xml-entities --output $(ONT)-merged.owl
	sed -i '.bak' 's/<owl:imports[^>]*\/>/<!-- & -->/' $(ONT)-merged.owl

# Make a file that provides labels and synonyms for each term in ontology from BFO:Entity .
	robot query --format TSV --input foodon-merged.owl -s foodon_synonyms.sparql foodon-synonyms.tsv

# ALL OWL imports: chebi efo envo eo gazetteer ncbitaxon obi ro uberon langual
# ALL OntoFox imports: chebi efo envo eo gazetteer ncbitaxon obi ro
# ISSUE WITH BFO: Ontofox doesn't download disjointWith axioms????
IMPORTS = chebi envo gaz general ncbitaxon
IMPORTS_OWL = $(patsubst %, imports/%_import.owl,$(IMPORTS))
IMPORTS_TXT = $(patsubst %, imports/%_ontofox.txt,$(IMPORTS))

# Make this target to regenerate ALL
all_imports: $(IMPORTS_OWL)

# Dependency between import.owl and ontofox.txt file. Only fetches .owl if it
# doesn't exist or if .txt has later timestamp.
# All annotations should only exist in the ontology-metadata.owl file and bfo.owl files.
# Because OWLAPI is erroniously parsing 1 line commented out annotations, make the "sed ..." step last.
# On Macs we need the '.bak' after -i to avoid a parsing error; while on other linux it fails
imports/%_import.owl: imports/%_ontofox.txt
	curl -s -F file=@imports/$*_ontofox.txt -o $@ http://ontofox.hegroup.org/service.php

	echo "Downloaded $@"

	$(ROBOT) reduce -i $@ -r ELK -o $@

	echo "Entity substitution."

	sed -i '.bak' 's/<owl:AnnotationProperty[^>]*\/>/<!-- & -->/; s/<\/owl:AnnotationProperty>/& -->/; s/<owl:AnnotationProperty[^>]*">/<!-- &/;' $@ ||	sed -i 's/<owl:AnnotationProperty[^>]*\/>/<!-- & -->/; s/<\/owl:AnnotationProperty>/& -->/; s/<owl:AnnotationProperty[^>]*">/<!-- &/;' $@

	echo "Removed annotation definitions."


# Any import .owl file that has a timestamp significantly later than .txt has been resaved by Protege or some other process.
# re-apply xml entities to it
# imports/%_import.owl: ???????
#	$(ROBOT) reduce -i @imports/$*_import.owl -r ELK -s true --xml-entities -o @imports/$*_import.owl


.PRECIOUS: imports/%_import.owl



## Make Targets for Robot Templates: ##

# Organismal Materials Robot template
imports/robot/plant_parts.owl: imports/robot/plant_parts.tsv
	$(ROBOT) template --template $< -i foodon-edit.owl --ontology-iri "http://purl.obolibrary.org/foodon/imports/robot_plant_parts.owl" -o $@.tmp.owl && mv $@.tmp.owl $@


release: $(ONT).owl
	cp $^ $RELEASEDIR && cp imports/*.owl $RELEASEDIR/imports
