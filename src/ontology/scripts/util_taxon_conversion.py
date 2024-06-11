#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# A delicate script to deprecate FoodON plant and animal organisms when they
# have a subclass axiom " 'in taxon' some [NCBITaxon organism]". The subclass
# axiom is removed, and the class is converted to be about the NCBITaxon 
# organism so that all Foodon annotations and other clauses about it are kept.
# References to the existing term are switched to the new NCBITaxon term. 
# A deprecation entry is created for the existing FoodOn class.
#
# The initial run of this in Sept 2020 converted about 1782 classes.
# Its ok to re-run this - it will do the conversion on any new plant or animal
# that has an 'in taxon' link to an NCBITaxon.
#
# Classes that have 'in taxon' (x or y or z...) are left alone.
#
# NOTE: Trick with result = elementTree.find(...) 
#  - it can only be tested with "if result != None:""
#
# NOTE: Currently this cannot be run twice on the same i/o file because save
# in first pass the xml: entity prefix is not included in entity declaration 
# section but file content still contains this prefix, so searches for xml:...
# error out.
#  
# NOTE: In case one needs to retrieve a good deprecation_import.owl file:
# This one not infected with output of 'in taxon' script:
# git show f0aed4b:src/ontology/imports/deprecated_import.owl > imports/deprecation_import.owl
#
#
# Order of operations:
# python util_taxon_conversion.py
# python util_obsoletion_update.py imports/deprecation_import.owl foodon-edit.owl
# python util_obsoletion_update.py imports/deprecation_import.owl imports/foodon_product_import.owl

# see https://stackabuse.com/reading-and-writing-xml-files-in-python/
import xml.etree.ElementTree as ET
import os

# .owl file to store new deprecations in
deprecated_file_path = 'imports/deprecation_import.owl';

# .owl file to look for items to be converted from foodon to ncbitaxon.
input_file_path = 'foodon-edit.owl'
output_file_path = input_file_path;

# Preserve comments in XML files: 
# https://stackoverflow.com/questions/33573807/faithfully-preserve-comments-in-parsed-xml
#class CommentedTreeBuilder(ET.TreeBuilder):
#    def comment(self, data):
#        self.start(ET.Comment, {})
#        self.data(data)
#        self.end(ET.Comment)
#
#parser = ET.XMLParser(target=CommentedTreeBuilder())

#Python 3.8
#parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
# problem is it errors out on owl rdf/xml

# Had to dig for this code to re-establish namespace from given XML file:
# Have to fetch existing file's dictionary of prefix:uri namespaces
namespace = dict([
    node for (_, node) in ET.iterparse(input_file_path, events=['start-ns'])
])
# Oddly this one can get dropped on write of file, so must add it:
namespace['xml'] = 'http://www.w3.org/XML/1998/namespace';

for prefix in namespace:
	ET.register_namespace(prefix, namespace[prefix]);

tree = ET.parse(input_file_path); 
root = tree.getroot();

deprecations = ET.parse(deprecated_file_path); # replaced ET.parse()
deprecation_root = deprecations.getroot();

# For working with ElementTree attributes, it seems we need to use this format of namespace:
ns = {
	'owl':  '{http://www.w3.org/2002/07/owl#}',
	'rdfs': '{http://www.w3.org/2000/01/rdf-schema#}',
	'rdf':  '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}',
	'obo':  '{http://purl.obolibrary.org/obo/}'
}

#
# IN deprecation_root owl file, add an XML/RDF owl:Class about about_uri, with
# replaced_by link to owl_taxon_uri.
#
def deprecate_term(deprecation_root, about_uri, label, owl_taxon_uri):

	# One extra check could be done to ensure duplicate deprecation not added.
	obs_element = deprecation_root.makeelement('owl:Class', {'rdf:about': about_uri});

	deprecated = obs_element.makeelement('owl:deprecated', {'rdf:datatype': 'http://www.w3.org/2001/XMLSchema#boolean'});
	deprecated.text = 'true';
	obs_element.append(deprecated);

	obs_label = obs_element.makeelement('rdfs:label', {'xml:lang':'en'});
	obs_label.text = 'obsolete: ' + label.text;
	obs_element.append(obs_label);

	# "replaced by" (IAO:0100001) taxonomy term.
	replaced = obs_element.makeelement('obo:IAO_0100001', {'rdf:resource':owl_taxon_uri});
	obs_element.append(replaced);

	deprecation_root.append(obs_element);



rdf_resource_lookup = {};
# Create index on all owl:Class/rdfs:subClassOf[@rdf:resource tags; only needs 1 entry
for tag in root.findall('owl:Class/rdfs:subClassOf[@rdf:resource]', namespace):
	rdf_resource_lookup[tag.attrib['{rdf}resource'.format(**ns)]] = [tag];


# For all classes in main ontology file, see if they are FoodOn uri's and have
# an "'in taxon' some [taxon]" axiom, and if so, convert class to be about 
# [taxon], and deprecate the class'es existing uri in deprecated terms owl file

count = 0;

for owl_class in root.findall('owl:Class', namespace):
	about = owl_class.get('{rdf}about'.format(**ns)); # owl_class.attrib['{rdf}about'.format(**ns)];
	if (about and 'FOODON_' in about):

		# ONLY DO TAXON CONVERSION IF THIS CLASS HAS NO EXPLICIT SUBCLASSES.
		# PARENT CONVERSIONS MUST BE MANUALLY REVIEWED - TOO OFTEN THEY HAVE
		# THEMSELVES AS A CHILD if 'in taxon' Y is too general.
		if about in rdf_resource_lookup:
			continue;

		# Here we're only dealing with a leaf (no subclasses)
		for owl_subclassof in owl_class.findall('rdfs:subClassOf', namespace):

			for owl_restriction in owl_subclassof.findall('owl:Restriction', namespace):

				# Find 'in taxon'
				owl_property = owl_restriction.find('owl:onProperty[@rdf:resource = "http://purl.obolibrary.org/obo/RO_0002162"]', namespace)

				if owl_property != None:
					owl_taxon = owl_restriction.find('owl:someValuesFrom[@rdf:resource]', namespace);

					if owl_taxon != None:

						owl_taxon_uri = owl_taxon.attrib['{rdf}resource'.format(**ns)];

						label = owl_class.find('rdfs:label[@xml:lang="en"]', namespace);
						if label != None:
							# Not converting items that are animal /human as consumer
							if label.text.find('consumer') != -1: 
								print ("Skipping consumer ", label.text);
								continue;

							alt_term = owl_class.makeelement('obo:IAO_0000118', {'xml:lang': 'en'});
							alt_term.text = label.text;
							owl_class.append(alt_term);
							owl_class.remove(label);

						# HERE WE MAKE CHANGES
						# FoodOn plant and animal organism may have duplicate dbxref to taxon:
						taxon_xref = owl_class.find('oboInOwl:hasDbXref[@rdf:resource = "'+owl_taxon_uri+'"]', namespace)
						if taxon_xref != None:
							#print ('found dbxref')
							owl_class.remove(taxon_xref);


						# Remove existing rdf:about and add new one to class:
						owl_class.attrib.pop('{rdf}about'.format(**ns), None)
						owl_class.set('rdf:about', owl_taxon_uri);

						# Remove 'in taxon' some NCBITaxon axiom
						owl_class.remove(owl_subclassof);

						# Prepare the obsoleted FoodOn class
						deprecate_term(deprecation_root, about, label, owl_taxon_uri);

						count += 1;

					else:
						print ("Skipped ", about, "as it has multiple taxa expression");

print ('Processed', count , 'taxa conversions.');

# 2nd pass eliminates synonomy tags and IAO:0000118 alternate term tags that match rdfs:label
for owl_class in root.findall('owl:Class', namespace):
	label_node = owl_class.find('rdfs:label[@xml:lang="en"]', namespace);
	if label_node != None:
		label = label_node.text.lower();
		# Housecleaning: get rid of synonyms that match label
		# See https://docs.python.org/2/library/xml.etree.elementtree.html
		for synonymy in ['oboInOwl:hasSynonym','oboInOwl:hasExactSynonym', 'obo:IAO_0000118']:
			for synonym in owl_class.findall(synonymy, namespace):
				if not synonym.text:
					# Sometimes synonym has URI by accident instead of text
					print ("Error in ", synonymy, "in",label);
					pass

				elif synonym.text.lower() == label:
					print ("Found duplicate", synonymy, label);
					owl_class.remove(synonym);


if (count > 0):
	tree.write(output_file_path, xml_declaration=True, encoding='utf-8', method="xml", );
	cmd = f'robot reduce -i {output_file_path} -r ELK -o {output_file_path}' # Note no --xml-entities
	os.system(cmd)

	deprecations.write(deprecated_file_path, xml_declaration=True, encoding='utf-8', method="xml");
	cmd = f'robot reduce -i {deprecated_file_path} -r ELK -o {deprecated_file_path}' # Note no --xml-entities
	os.system(cmd)

