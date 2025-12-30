
# If term replaced by obo:IAO_0100001 some other term, 
# In foodon-edit.ofn:

#   ADD: ?replacement oboInOwl:hasSynonym, oboInOwl:hasExactSynonym, obo:IAO_0000118 alternative label, oboInOwl:hasDbXref, obo:IAO_0000115 definition,
#   DROP ?x everything about it from foodon-edit.ofn (incl. obo:IAO_0000114 curation status)
#
# In ../imports/foodon-deprecate.ofn add:
#   ?x rdfs:label as "obsolete: " + label, 
#	?x obo:IAO_0100001 ?replacement # replaced by
#   ?x owl:deprecated true
#
# ASSUMES INPUT ONTOLOGY DOESN'T INCLUDE deprecation_import.ofn:

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

# Spotting entries that have replacement from a FOODON term to another where
# neither term has been marked owl:deprecated yet. 
BASE_DEPRECATE = """
WHERE {
		{?x obo:IAO_0100001 ?replacement}. # replaced by
		FILTER (NOT EXISTS {?x owl:deprecated true})
		FILTER (NOT EXISTS {?replacement owl:deprecated true})
		FILTER (STRSTARTS(STR(?x), "http://purl.obolibrary.org/obo/FOODON_")).
		{?x rdfs:label ?label} 

"""

QUERIES = { # Sparql 1.1 (which Protege snap sparql doesn't quite support )

	'add_deprecation_label': {
		'active': True,
		'type': 'INSERT',
		'target': '../imports/deprecation_import.ofn',
		'query': "SELECT DISTINCT ?x (rdfs:label as ?predicate) (CONCAT('obsolete: ', ?label) as ?new_label)" + BASE_DEPRECATE + "}"
	},

	'add_replaced_by': { # Annotation
		'active': True,
		'type': 'INSERT',
		'target': '../imports/deprecation_import.ofn',
		'query': "SELECT DISTINCT ?x (obo:IAO_0100001 as ?predicate) ?replacement" + BASE_DEPRECATE + "}"
	},

	'add_deprecated': { # Annotation
		'active': True,
		'type': 'INSERT',
		'target': '../imports/deprecation_import.ofn',
		'query': "SELECT DISTINCT ?x (owl:deprecated as ?predicate) ('true'^^xsd:boolean as ?true)" + BASE_DEPRECATE + "}"
	}, # ('true'^^xsd:boolean as ?true)

	# Done on separate file - this clears out ALL predicates of subject ?x
	# EXCEPTION: "in taxon some ..." leads to blank node, so drop that.
	# WHY DIDN'T THIS delete owl:subClassOf ???
	'delete_from_foodon-edit': {
		'active': True,
		'type':'DELETE',
		'target': '../foodon-edit.ofn',
		'query': "SELECT DISTINCT ?x ?predicate ?object " + BASE_DEPRECATE + """
		{?x ?predicate ?object}.
		FILTER (!isBlank(?object))
	}"""},

	# ?replacement oboInOwl:hasSynonym, oboInOwl:hasExactSynonym, obo:IAO_0000118 alternative label, oboInOwl:hasDbXref, obo:IAO_0000115 definition, IAO_0000114 has curation status
	'add_annotations': { # oboInOwl#hasDbXref
		'active': True,
		'type': 'INSERT',
		'target': '../foodon-edit.ofn',# curation status, alternative label, definition
		'query': "SELECT DISTINCT ?replacement ?predicate ?text" + BASE_DEPRECATE + """
		VALUES ?predicate { oboInOwl:hasSynonym oboInOwl:hasExactSynonym obo:IAO_0000118 oboInOwl:hasDbXref obo:IAO_0000115}.
		{?x ?predicate ?text}.
	}"""},

}

TEST = {


}

###############################################################################
if __name__ == "__main__":


	CACHED_ONTOLOGY = "cache-foodon-merged.owl"; # has merged version of FoodOn.

	print("Freshening", CACHED_ONTOLOGY); # Ensure latest report is available
	subprocess.check_output(["robot", "merge", "--input", "../foodon-edit.ofn", 'reason', '--reasoner','ELK','--exclude-duplicate-axioms', "relax","--output", CACHED_ONTOLOGY]);

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


