content_dict = {};


print ("Removing duplicate labels as introduced by CDNO (for CHEBI), etc. ")

# Break input file of lines like '<http://purl.obolibrary.org/obo/CHEBI_5054>	"fibrin"@en' into key/value
with open('foodon-merged-modify.txt', 'r') as lookup_handler:
    for line in lookup_handler:
    	if len(line) > 1:
	    	if ("\t" in line):
	    		# Future: allow value to be substitution expression.
	    		(key, val) = line.split("\t");
	    		val = val[:-1]; # trim \n from val.
	    	else:
	    		key = line.strip();
	    		val = "";

	    	content_dict[key] = val;

new_content = "";
with open('foodon-merged.ofn', 'r') as foodon_merged:

	for line in foodon_merged.read().splitlines():
		# Find eg 'AnnotationAssertion(rdfs:label obo:CHEBI_XXXXXX "......"@en)'
		if line.strip() in content_dict:
			continue;

		# Here we preserve line in output.
		new_content += line + "\n";

with open('foodon-merged.ofn', 'w') as foodon_merged:
	foodon_merged.write(new_content)