#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# A script which takes in an owl rdf/xml file of deprecated terms, and searches
# in another owl rdf/xmo file for references to those terms, and replaces them 
# with the new "replaced by" (IAO:0100001) term, and saves result.
# 
# This file can be run repeatedly to keep up to date on deprecations.
#
# Command line parameters:
# argv[0]: path to deprecated terms .owl file.
# argv[1]: path of ontology file to update rdf:resource links in.
#
# example: 
# python util_obsoletion_update.py imports/deprecation_import.owl foodon-edit.owl
# python util_obsoletion_update.py imports/deprecation_import.owl imports/foodon_product_import.owl
#



# For CRUD see https://stackabuse.com/reading-and-writing-xml-files-in-python/
import xml.etree.ElementTree as ET
import sys
from os import path
import os

if len(sys.argv) < 2:
	sys.exit('Help Info:\n util_obsoletion_update.py [deprecated term .owl file path] [path of ontology to update rdf:resource links in]')

deprecated_file_path = sys.argv[1];
input_file_path = sys.argv[2];
output_file_path = sys.argv[2];

if not path.exists(deprecated_file_path):
	sys.exit('Unable to locate deprecated ontology term file: ' + deprecated_file_path);

if not path.exists(input_file_path):
	sys.exit('Unable to locate ontology update file: ' + input_file_path);


# Had to dig for this code to re-establish namespace from given XML file:
# Have to fetch existing file's dictionary of prefix:uri namespaces
namespace = dict([
    node for (_, node) in ET.iterparse(input_file_path, events=['start-ns'])
]);
for prefix in namespace:
	ET.register_namespace(prefix, namespace[prefix]);


deprecations = ET.parse(deprecated_file_path);
deprecation_root = deprecations.getroot();

tree = ET.parse(input_file_path);
root = tree.getroot();

print ("Using deprecated terms from:", deprecated_file_path)
print ("Updating ontology:", input_file_path)

# For working with ElementTree attributes, it seems we need to use this format of namespace:
ns = {
	'owl':  '{http://www.w3.org/2002/07/owl#}',
	'rdfs': '{http://www.w3.org/2000/01/rdf-schema#}',
	'rdf':  '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}',
	'obo':  '{http://purl.obolibrary.org/obo/}'
}

change = True;
any_output = False;
while change:

	rdf_resource_lookup = {};
	# Create index on all rdf:resource tags
	# Issue: search string is a bit odd because it looks for all children with // 
	# but adds * children because error generated if you don't mention a tag name.
	#
	for tag in root.findall('.//*[@rdf:resource]', namespace):

		# Somehow some tags are matching above, but tag.attrib doesn't work, esp.
		# with "someValuesFrom" tags?
		try: 
			target = tag.attrib['{rdf}resource'.format(**ns)];
			if target in rdf_resource_lookup:
				rdf_resource_lookup[target].push(tag)
			else:
				rdf_resource_lookup[target] = [tag];
			#print ("adding", target)
		except:
			continue;

	change = False;
	count = 0;

	# Look in all deprecated .owl file classes that have a replacement iri,
	for deprecated_cursor in deprecation_root.findall('owl:Class', namespace):
		about = deprecated_cursor.attrib['{rdf}about'.format(**ns)];

		# Find any that are mentioned to be deprecated (not bothering with boolean value).
		for owl_deprecated in deprecated_cursor.findall('owl:deprecated', namespace):
			# Term replaced by IRI
			# Note that sometimes an "X or Y ..." disjunction term can have two or
			# more replacement axioms. Not sure whats best for that case.

			owl_replacements = deprecated_cursor.findall('obo:IAO_0100001', namespace);
			if owl_replacements:
				# Almost all deprecations have only one replacement
				if owl_replacements[0].text:
					replaced_iri = owl_replacements[0].text;
				else:	
					replaced_iri = owl_replacements[0].attrib['{rdf}resource'.format(**ns)]

				if (about in rdf_resource_lookup):
					# Look in target ontology for resource iri to replace
					for tag in rdf_resource_lookup[about]:
						# Replace existing rdf:resource tag:
						tag.attrib.pop('{rdf}resource'.format(**ns), None)
						tag.set('rdf:resource', replaced_iri);
						#print ('Updated', about, 'to', replaced_iri);
						change = True;
						any_output = True;
						count += 1;

	print ('Updated', count , 'rdf:resource references.');

if (any_output):
	tree.write(output_file_path, xml_declaration=True, encoding='utf-8', method="xml");
	# This reestablishes OWL-API comments etc. as though saved by protege, to cut down on git diff.
	cmd = f'robot reduce -i {output_file_path} -r ELK -o {output_file_path}'; # Note no --xml-entities
	print (cmd)
	os.system(cmd)

"""


<owl:intersectionOf rdf:parseType="Collection">
	<owl:Class>
	    <owl:intersectionOf rdf:parseType="Collection">
	        <owl:Restriction>
	            <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0001000"/>
	            <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/UBERON_0000178"/>
	        </owl:Restriction>
	        <owl:Restriction>
	            <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0003001"/>
	            <owl:someValuesFrom>
	                <owl:Class>
	                    <owl:unionOf rdf:parseType="Collection">
	                        <rdf:Description rdf:about="http://purl.obolibrary.org/obo/FOODON_03411136"/>
	                        <rdf:Description rdf:about="http://purl.obolibrary.org/obo/FOODON_03411161"/>
	                    </owl:unionOf>
	                </owl:Class>
	            </owl:someValuesFrom>
	        </owl:Restriction>
	    </owl:intersectionOf>
	</owl:Class>


	    <owl:Class rdf:about="http://purl.obolibrary.org/obo/FOODON_03312067">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/FOODON_00001605"/>
        <rdfs:subClassOf>
            <owl:Class>
                <owl:unionOf rdf:parseType="Collection">
                    <owl:Restriction>
                        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0001000"/>
                        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/FOODON_03411161"/>
                    </owl:Restriction>
                    <owl:Restriction>
                        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0001000"/>
                        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/NCBITaxon_9823"/>
                    </owl:Restriction>
                </owl:unionOf>
            </owl:Class>
        </rdfs:subClassOf>



"""