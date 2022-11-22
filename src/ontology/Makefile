#!/bin/sh
OBO=http://purl.obolibrary.org/obo
ONT=foodon
BASE=$(OBO)/$(ONT)
SRC=$(ONT)-edit.ofn
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
reason: foodon-merged.ofn
	$(ROBOT) reason --input $< --reasoner ELK --equivalent-classes-allowed none

prepare_release: all
#	cp $(ONT).owl $(RELEASEDIR)
#	cp imports/*.ofn $(RELEASEDIR)/imports/ &&\

	$(ROBOT) convert --input $(ONT)-merged.ofn --xml-entities --output $(RELEASEDIR)/$(ONT).owl &&\
	cp $(ONT)-base.owl $(RELEASEDIR) &&\
	cp foodon-synonyms.tsv $(RELEASEDIR)

#	(test -f subsets/*.owl && cp subsets/*.owl $(RELEASEDIR)/subsets && git add $(RELEASEDIR)/subsets/* || echo "no subsets") &&\
	echo "Release files are now in $(RELEASEDIR) - now you should commit, push and make a release on github"

#make
#Annotates foodon.owl ( $(ONT).owl ) file with release
$(ONT).owl: $(SRC)
	$(ROBOT) annotate --input $(SRC) --xml-entities -V $(BASE)/releases/`date +%Y-%m-%d`/$(ONT).owl --typed-annotation http://purl.org/dc/terms/date `date +%Y-%m-%d` xsd:date --output $(SRC)

# Merge ontology and imports into one file, and commment out imports tags
# production merged file shouldn't allow equivalent classes but sometimes
# import files have them.
# remove --select "owl:NamedIndividual=obo:BFO_*"
	$(ROBOT) merge --input $(SRC) --xml-entities reason --reasoner ELK --equivalent-classes-allowed false relax reduce --output $(ONT)-merged.ofn

	python3 foodon-merged-remediation.py

# Make foodon-base.owl, stripped of food product import tags and files
# Add GAZ?
	$(ROBOT) remove --input $(ONT)-edit.ofn --select imports --trim true annotate --ontology-iri "http://purl.obolibrary.org/obo/foodon-base.owl" merge --input imports/general_import.ofn --input imports/ncbitaxon_import.ofn --input imports/envo_import.ofn --input imports/chebi_import.ofn --input imports/robot_process.ofn convert --output $(ONT)-base.owl


# Make a file that provides labels and synonyms for each term in ontology from BFO:Entity down.
#	$(ROBOT) convert --input $(ONT)-merged.ofn --output $(ONT)-merged.owl
#	$(ROBOT) query --format TSV --input $(ONT)-merged.owl -s foodon_synonyms.sparql foodon-synonyms.tsv
#	rm $(ONT)-merged.owl
	$(ROBOT) query --format TSV --input $(ONT)-merged.ofn -s foodon_synonyms.sparql foodon-synonyms.tsv

# ALL OntoFox imports: chebi efo envo eo gazetteer ncbitaxon obi ro
# ISSUE WITH BFO: Ontofox doesn't download disjointWith axioms????
IMPORTS = chebi envo gaz general ncbitaxon
IMPORTS_OWL = $(patsubst %, imports/%_import.ofn,$(IMPORTS))
IMPORTS_TXT = $(patsubst %, imports/%_ontofox.txt,$(IMPORTS))

# Make this target to regenerate ALL
all_imports: $(IMPORTS_OWL)

# Dependency between import.owl and ontofox.txt file. Only fetches .owl if it
# doesn't exist or if .txt has later timestamp.
# All annotations should only exist in the ontology-metadata.owl file and bfo.owl files.
# Because OWLAPI is erroniously parsing 1 line commented out annotations, make the "sed ..." step last.
# On Macs we need the '.bak' after -i to avoid a parsing error; while on other linux it fails

imports/%_import.ofn: imports/%_ontofox.txt
	curl -s -F file=@imports/$*_ontofox.txt -o $*_import.owl https://ontofox.hegroup.org/service.php
	echo "Downloaded *_import.owl"

#	$(ROBOT) reduce --input $@ --reasoner ELK --output $@
	$(ROBOT) reduce --input $*_import.owl --xml-entities --reasoner ELK --output imports/$*_import.ofn
	rm $*_import.owl

#	echo "Entity substitution."

#	sed -i '.bak' 's/<owl:AnnotationProperty[^>]*\/>/<!-- & -->/; s/<\/owl:AnnotationProperty>/& -->/; s/<owl:AnnotationProperty[^>]*">/<!-- &/;' $@ ||	sed -i 's/<owl:AnnotationProperty[^>]*\/>/<!-- & -->/; s/<\/owl:AnnotationProperty>/& -->/; s/<owl:AnnotationProperty[^>]*">/<!-- &/;' $@

#	echo "Removed annotation definitions."

.PRECIOUS: imports/%_import.ofn



## Make Targets for Robot Templates: ##

# Organismal Materials Robot template
imports/robot/plant_parts.ofn: imports/robot/plant_parts.tsv
	$(ROBOT) template --template $< -i foodon-merged.ofn --ontology-iri "http://purl.obolibrary.org/foodon/imports/robot_plant_parts.ofn" -o $@.tmp.owl && mv $@.tmp.owl $@


release: $(ONT).owl
	cp $^ $RELEASEDIR && cp imports/*.owl $RELEASEDIR/imports
