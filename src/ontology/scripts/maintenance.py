# OUTDATED. A better approach is to:
# - string ROBOT commands, demonstrated in robot_maintenance.py
# - or to use OWLReady2, demonstrated in foodon_table.py

import json
import sys
import os
import optparse

#from ontohelper import OntoHelper as oh
import ontohelper as oh

import rdflib
from rdflib.plugins.sparql import prepareQuery

# Do this, otherwise a warning appears on stdout: No handlers could be 
#found for logger "rdflib.term"
import logging; logging.basicConfig(level=logging.ERROR) 

from collections import OrderedDict

def stop_err(msg, exit_code = 1):
	sys.stderr.write("%s\n" % msg)
	sys.exit(exit_code)


import rdflib
from rdflib.plugins.sparql import prepareQuery

""" 
Add these PREFIXES to Sparql query window if you want to test a query there:

PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX OBO: <http://purl.obolibrary.org/obo/>
PREFIX xmls: <http://www.w3.org/2001/XMLSchema#>
""" 

def initQueries(namespace):
	return {
		##################################################################
		# Generic TREE "is a" hierarchy from given root.
		#
		'tree': prepareQuery("""
			SELECT DISTINCT ?id ?label ?parent_id ?deprecated ?replaced_by 
			WHERE {	
				?parent_id rdfs:subClassOf* ?root.
				?id rdfs:subClassOf ?parent_id.
				OPTIONAL {?id rdfs:label ?label}.
					OPTIONAL {?id GENEPIO:0000006 ?ui_label}. # for ordering
				OPTIONAL {?id owl:deprecated ?deprecatedAnnot.
					BIND(xsd:string(?deprecatedAnnot) As ?deprecated).
				}.
				OPTIONAL {?id IAO:0100001 ?replaced_byAnnot.
					BIND(xsd:string(?replaced_byAnnot) As ?replaced_by).
				}.	
			}
			ORDER BY ?parent_id ?ui_label ?label 
		""", initNs = namespace),


		# ################################################################
		# UI LABELS 
		# These are annotations directly on an entity.  This is the only place
		# that ui_label and ui_definition should really operate. Every entity
		# in OWL file is retrieved for their rdfs:label, IAO definition etc.
		'entity_text': prepareQuery("""

			SELECT DISTINCT ?label ?definition ?ui_label ?ui_definition
			WHERE {  
				{?datum rdf:type owl:Class} 
				UNION {?datum rdf:type owl:NamedIndividual} 
				UNION {?datum rdf:type rdf:Description}.
				OPTIONAL {?datum rdfs:label ?label.} 
				OPTIONAL {?datum IAO:0000115 ?definition.}
				OPTIONAL {?datum GENEPIO:0000006 ?ui_label.} 
				OPTIONAL {?datum GENEPIO:0000162 ?ui_definition.}
			} ORDER BY ?label
		""", initNs = namespace),

		##################################################################
		# Fetch ontology metadata fields
		#
		# Example ontology header:
		#	<owl:Ontology rdf:about="http://purl.obolibrary.org/obo/genepio.owl">
	    #	    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/genepio/releases/2018-02-28/genepio.owl"/>
	    #		<oboInOwl:default-namespace rdf:datatype="http://www.w3.org/2001/XMLSchema#string">GENEPIO</oboInOwl:default-namespace>
	    #		<dc:title xml:lang="en">Genomic Epidemiology Ontology</dc:title>
	    #		<dc:description xml:lang="en">The Ontology for Biomedical Investigations (OBI) is build in a ...</dc:description>
	    #		<dc:license rdf:resource="http://creativecommons.org/licenses/by/3.0/"/>
	    #		<dc:date rdf:datatype="http://www.w3.org/2001/XMLSchema#date">2018-02-28</dc:date>

		'ontology_metadata': prepareQuery("""
		SELECT DISTINCT ?resource ?title ?description ?versionIRI ?prefix ?license ?date 
		WHERE {
			?resource rdf:type owl:Ontology.
			OPTIONAL {?resource (dc:title|terms:title) ?title.}
			OPTIONAL {?resource (dc:description|terms:description) ?description.}
			OPTIONAL {?resource owl:versionIRI ?versionIRI.}
			OPTIONAL {?resource oboInOwl:default-namespace ?prefix.}
			OPTIONAL {?resource (dc:license|terms:license) ?license.}
			OPTIONAL {?resource (dc:date|terms:date) ?date.}
		}
		""", initNs = namespace)

	} # End of return object


# STRATEGY: 
# Issue: NCBITaxon has label :NCBITaxon_89953> Merluccius senegalensis
#,foodn-edit.ofn has duplicate: hasSynonym: merluccius senegalensis instance to
# delete.  Delete query won't work in robot unless imports are MERGED! 
# STRATEGY: Just calculate which items need removal.  Write delete query 
# targeting those known items directly. e.g. hasSynonym merluccius senegalensis"
# 
#Load ontology AND imports, run query to determine which entities need removing,
UPDATE_QUERIES = {
	'delete_duplicate_synonym': """
		PREFIX owl: <http://www.w3.org/2002/07/owl#>
		PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
		PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
		PREFIX oboInOwl: <http://www.geneontology.org/formats/oboInOwl#>
		PREFIX obo: <http://purl.obolibrary.org/obo/>

		DELETE {?x ?p ?syn}
		#SELECT ?x ?lab ?syn
		WHERE {
			{?x rdfs:label ?lab}.
			{?x oboInOwl:hasSynonym ?syn}.
			FILTER (LCASE(STR(?lab)) = LCASE(STR(?syn)) )
			{?x ?p ?syn}
		}
		""",
	}

if __name__ == "__main__":
	
	MAIN_ONTOLOGY_FILE = './foodon-edit.owl';

	onto_helper = oh.OntoHelper();

	onto_helper.queries = initQueries(onto_helper.namespace);

	try:
		# ISSUE: ontology file taken in as ascii; rdflib doesn't accept
		# utf-8 characters so can experience conversion issues in string
		# conversion stuff like .replace() below


		onto_helper.graph.parse(MAIN_ONTOLOGY_FILE, format='xml')

		#(main_ontology_file, output_file_basename) = self.onto_helper.check_ont_file(args[0], options)

	except Exception as e:
		#urllib2.URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
		stop_err('WARNING:' + MAIN_ONTOLOGY_FILE + " could not be loaded!\n", e)


	# Add each ontology include file (must be in OWL RDF format)
	self.onto_helper.do_ontology_includes(MAIN_ONTOLOGY_FILE)


	# Load self.struct with ontology metadata
	onto_helper.set_ontology_metadata(onto_helper.queries['ontology_metadata'])
	print ('Metadata: ' + json.dumps(onto_helper.struct['metadata'],  sort_keys=False, indent=4, separators=(',', ': ')) );

	# ISSUE: DELETE QUERY WILL ONLY WORK ON MERGED FILE, NOT ON foodon-edit.ofn 
	#onto_helper.graph.update(UPDATE_QUERIES['delete_duplicate_synonym']);

	print ("FINISHED QUERIES");

	# Using 'pretty-xml' provides better formatting than just 'xml'
	#onto_helper.graph.serialize(destination='../test.owl', format="pretty-xml")
