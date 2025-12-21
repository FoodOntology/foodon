import subprocess
import tempfile

QUERIES = {
	'duplicate_synonym': {
		'type': 'delete',
		'target': '../foodon-edit.owl',
		'relation': '<http://www.geneontology.org/formats/oboInOwl#hasSynonym>',
		'query': """
			PREFIX owl: <http://www.w3.org/2002/07/owl#>
			PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
			PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
			PREFIX oboInOwl: <http://www.geneontology.org/formats/oboInOwl#>
			PREFIX obo: <http://purl.obolibrary.org/obo/>
			SELECT ?x ?string
			WHERE {
				VALUES ?predicate { oboInOwl:hasSynonym }
				{?x rdfs:label ?label}.
				{?x ?predicate ?string}.
				FILTER (LCASE(STR(?label)) = LCASE(STR(?string)) )
			}
		"""
	},
}

###############################################################################
if __name__ == "__main__":

	CACHED_ONTOLOGY = "cache-foodon-merged.owl"; # has merged version of FoodOn.

	# Creates a temporary file in /var/folders/73/
	# PROBLEM, CAN't read it as well as write to it.  Subprocess.run doesn't see file with content.
	#with tempfile.NamedTemporaryFile(mode='w+', suffix=".sparql") as tmp_file: # delete=False, 

	# We create a series of insert and delete queries mainly on foodon-edit.ofn; 
	# All queries are bundeld into one robot query / save command.

	for query_name, query_obj in QUERIES.items():
		#query = query_obj['query'];
		target_ontology_file = query_obj['target'];

		# Cant read from this file while it is in a where loop?
		with open('temp.sparql', 'w') as tmp_file:
			tmp_file.write(query_obj['query']) # Write the text content

		tmp_file_name = tmp_file.name;

		try:
			# robot query --input cache-foodon-merged.owl --query temp.sparql temp.tsv
			subprocess.run(f"robot query -i {CACHED_ONTOLOGY} --query {tmp_file_name} temp.tsv", shell=True, check=True);

		except Exception as e:
			print("ERROR IN ROBOT", e);

		with open('temp.tsv', 'r') as tmp_file:
			bindings = tmp_file.read() # Write the text content

			bindings = '(' + bindings.replace('\n',') (');
			print (bindings);
			if query_obj['delete']:
				query_text = """
				DELETE WHERE {
					VALUES (?subject ?object) { {bindings}}
					?subject {query_ob['relation']} ?object .
				}			  	#FILTER(LANG(?object) = "en")
			"""

			with open('temp2.sparql', 'w') as tmp2_file:
				tmp2_file.write(query_text) # Write the text content

		subprocess.run(f"robot query -i {query_obj['target']} --query temp2.sparql --output temp.ofn", shell=True, check=True);

		#file_path = tmp_file.name





	#robot query --update ../sparql/delete_duplicate_hasSynonym.sparql --input cache-foodon-merged.ofn --output test.tsv