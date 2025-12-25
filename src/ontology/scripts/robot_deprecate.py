import subprocess
import tempfile

PREFIXES = """
	PREFIX owl: <http://www.w3.org/2002/07/owl#>
	PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
	PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
	PREFIX oboInOwl: <http://www.geneontology.org/formats/oboInOwl#>
	PREFIX obo: <http://purl.obolibrary.org/obo/>
	PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
""";

# Tricky: When queries are made on foodon-edit.ofn all by itself, without 
# imports then there may not be enough information for rdflib to determine
# if an object is a class, in which case it assumes its an instance, and
# perhaps puns an instance into a class, or visa versa.  So must establish
# the classes of things up front, stand-alone. 
# This query guarantees a deprecated item connected to an NCBITaxon which
# has one or more parents on the FOODON side.
BASE_NCBITAXON = """
WHERE {
		{?x owl:deprecated true}.
		# {?x rdf:type owl:Class}.
		{?x obo:IAO_0100001 ?replacement}. # replaced by
		# {?replacement rdf:type owl:Class}.
		FILTER (STRSTARTS(STR(?replacement), "http://purl.obolibrary.org/obo/NCBITaxon_")).
		{?replacement rdfs:subClassOf ?animal_parent}.
		FILTER (STRSTARTS(STR(?animal_parent), "http://purl.obolibrary.org/obo/FOODON_")).
"""

# Kill all oboInOwl:hasSynonym, oboInOwl:hasExactSynonym, alt label obo:IAO_0000118 which textually match label
# If term replaced by obo:IAO_0100001 some other term, 
#   MOVE its oboInOwl:hasSynonym, oboInOwl:hasExactSynonym, obo:IAO_0000118, oboInOwl:hasDbXref, obo:IAO_0000115, 
#        to target term ID.
#   DROP everything about it from foodon-edit.ofn (incl. obo:IAO_0000114 curation status)
#   MOVE its rdfs:label as "obsolete: " + label, obo:IAO_0100001
#   MARK it as owl:deprecated
#   


# Alternative label: http://purl.obolibrary.org/obo/IAO_0000118
QUERIES = { # Sparql 1.1 (which Protege snap sparql doesn't quite support )

	'attach_animal_parent': {
		'active': True,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?x (rdfs:subClassOf as ?predicate) ?animal_parent" + BASE_NCBITAXON + "}"
	},
	
	# This is wrong for classes. It creates instances instead of both subject and object.
	# Instead Need to add equivalent of this to foodon-edit.ofn :
	# SubClassOf(obo:FOODON_00001155 ObjectSomeValuesFrom(obo:RO_0002162 obo:NCBITaxon_3654))
	'add_in_taxon': { # RO_0002162 
		'active': False,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?x (obo:RO_0002162 as ?predicate) ?replacement" + BASE_NCBITAXON + "}"
	},

	'add_shorter_label': { # RO_0002162 ; takes off "obsolete: " prefix
		'active': True,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?x (rdfs:label as ?predicate) (SUBSTR(?label, 11) as ?new_label)" + BASE_NCBITAXON + """
		?x rdfs:label ?label
	}"""},

	'add_annotations': { # oboInOwl#hasDbXref
		'active': True,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',# curation status, alternative label, definition
		'query': "SELECT DISTINCT ?x ?predicate ?text" + BASE_NCBITAXON + """
		VALUES ?predicate { obo:IAO_0000114 obo:IAO_0000118 obo:IAO_0000115 oboInOwl:hasDbXref oboInOwl:hasSynonym oboInOwl:hasExactSynonym}
		?replacement ?predicate ?text
	}"""},
	# MOVE taxa Definition and alternative label and curation status: DELETE / INSERT

	'drop_annotations': {
		'active': True,
		'type': 'DELETE',
		'target': '../foodon-edit.ofn', 
		'query': "SELECT DISTINCT ?replacement ?predicate ?text" + BASE_NCBITAXON + """
		VALUES ?predicate { obo:IAO_0000114 obo:IAO_0000118 obo:IAO_0000115 oboInOwl:hasDbXref oboInOwl:hasSynonym oboInOwl:hasExactSynonym}
		?replacement ?predicate ?text
	}"""},

	'undo_tax_animal_parent': {
		'active': True,
		'type': 'DELETE',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?replacement (rdfs:subClassOf as ?predicate) ?animal_parent" + BASE_NCBITAXON + "}"
	},

	# Done on separate file - this clears out ALL predicates of subject ?x
	'delete_deprecation': {
		'active': True,
		'type':'DELETE',
		'target': '../imports/deprecation_import.ofn',
		'query': "SELECT DISTINCT ?x ?predicate ?object " + BASE_NCBITAXON + """
		{?x ?predicate ?object}
		}"""},# OPTIONAL {?child rdfs:subClassOf ?replacement}. FILTER (!bound(?child)).


	'delete_taxa_individual': { # RO_0002162 
		'active': True,
		'type': 'DELETE',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?replacement (rdf:type as ?predicate) (owl:NamedIndividual as ?object)" + BASE_NCBITAXON + "}"
	},
	# NOT WORKING.
	'assert_taxa_class': { # RO_0002162 
		'active': True,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?replacement (rdf:type as ?predicate) (owl:Class as ?object)" + BASE_NCBITAXON + "}"
	},

	'duplicate_synonym': {
		'active': True,
		'type': 'DELETE',
		'target': '../foodon-edit.ofn',
		'query': """
		SELECT DISTINCT ?x ?predicate ?string
		WHERE {
			VALUES ?predicate { oboInOwl:hasSynonym oboInOwl:hasExactSynonym oboInOwl:hasNarrowSynonym oboInOwl:hasBroadSynonym obo:IAO_0000118}
			{?x rdfs:label ?label}.
			{?x ?predicate ?string}.
			FILTER (LCASE(STR(?label)) = LCASE(STR(?string)) ).
		}"""},



}

TEST = {



}

###############################################################################
if __name__ == "__main__":

	CACHED_ONTOLOGY = "cache-foodon-merged.owl"; # has merged version of FoodOn.

	# Creates a temporary file in /var/folders/73/
	# PROBLEM, CAN't read it as well as write to it.  Subprocess.run doesn't see file with content.
	#with tempfile.NamedTemporaryFile(mode='w+', suffix=".sparql") as tmp_file: # delete=False, 

	# We create a series of insert and delete queries mainly on foodon-edit.ofn; 
	# All queries are bundeld into one robot query / save command.

	query_counter=1;
	updates = {};
	for query_name, query_obj in QUERIES.items():

		if not query_obj['active']:
			continue

		target_ontology_file = query_obj['target'];

		# Writing the select query which fetches subject and object of triples
		# to insert or delete.
		with open('temp.sparql', 'w') as tmp_file:
			tmp_file.write(PREFIXES + query_obj['query']) # Write the text content

		tmp_file_name = tmp_file.name;

		try:
			# robot query --input cache-foodon-merged.owl --query temp.sparql temp.tsv
			subprocess.run(f"robot --add-prefixes prefixes.json --xml-entities query -i {CACHED_ONTOLOGY} --query {tmp_file_name} temp.tsv", shell=True, check=True);

		except Exception as e:
			print("ERROR IN ROBOT", e);

		with open('temp.tsv', 'r') as tmp_file:
			bindings = tmp_file.readlines() # Write the text content
			print();

			bindings = bindings[1:]; # skip header
			binding_string = '';

			for binding in bindings:
				binding = binding.strip();
				binding = binding.replace('\t',' '); # get rid of \t
				binding_string += '\n(' + binding + ')';		

			query_type = query_obj['type'];
			query_text = f"""{query_type} {{?subject ?predicate ?object}}
			WHERE {{ VALUES (?subject ?predicate ?object) {{ {binding_string}\n }} }}""";

			with open(f"temp_{query_counter}.sparql", 'w') as tmp_query_file:
				tmp_query_file.write(query_text) # Write the text content
		
		print ('BUILT temp_' + str(query_counter) + '.sparql:', query_name, 'triples:', len(bindings)-1);
		if not target_ontology_file in updates:
			updates[target_ontology_file] = '';

		updates[target_ontology_file] += f' --update temp_{query_counter}.sparql';
		query_counter += 1;

	if len(updates):
		for input_file, update_param in updates.items():
			target = "temp_" + input_file.rsplit('/', 1)[1];
			# Perform all queries in a series, then save in single output file as ...
			# NOTE ISSUE: If input ontology file has some import that isn't mentioned in catalog file, robot throws a fit, even though its not given the instruction to bring in all the imports.
			command_line = f"robot --add-prefixes prefixes.json --xml-entities query -i {input_file} {update_param} -o {target}";
			# To run a single query manually:
			#robot --add-prefixes prefixes.json --xml-entities query -i ../foodon-edit.ofn --update temp_query_ncbitaxon_owlthing.sparql -o ../foodon-edit-2.ofn
			print ("EXECUTING QUERIES:", command_line)
			subprocess.run(command_line, shell=True, check=True);

	else:
		print ("Nothing updated!");


