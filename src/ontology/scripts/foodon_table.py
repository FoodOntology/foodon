# foodon_table.py
#
# This script supports the animal, plant, fungi templates by creating a robot
# file containing the following links for each {organism} in template 
# specification (it also has a menu hierarchy "parent" specified in its 
# template table row). 
#
# STEP 1:
# Extract one hierarchy which is recognized as whole animal or plant or fungi
# references. This is used to lookup nearest parent neighbour for a given
# organism to its [organism] material or [organism] food product parent.
# Note whole organism may have plain language taxa qualifiers, e.g. "lake trout
# (brown trout variety)"
#
# NEED separate animal / plant/ algae / fungus hierarchy in order to trace animal parenthood clearly.
#
# 	- "animal" FOODON_00003004 (whole organism, common name)
#       We actually just want the following, as categories like "companion animal"
#       are not directly relevant to whole organism food source.
#
# 		- "vertebrate animal" FOODON_03411297 
#		- "invertebrate animal" FOODON_00002452
#       - "animal (shell on)" FOODON_02022094
#
#      Don't want this - people should manually construct the merger if they
#	   need it - but currently it has hardcoded children to move:
#	    - "fish or lower water animal" FOODON_03411021
#
#      - Issue: cow food product vs beef food product
#	
#	- "whole plant"
#	   - "plant by taxonomy" FOODON_03413357 (common name)
#		 	- handle "brassica species" etc.
#
# 	- "algae" FOODON_03411301
#
#	- "fungus" FOODON_03411261
#
# STEP 2:
#
# If it has a corresponding "[organism] food product" class (which can be
# created by the organism template menu):
#
#   1) We need to link this to nearest "{organism parent} food product".
#      consequently must search through parents to find first one with 
#      "food product" suffix, e.g. "chicken -> "poultry" , "poultry food product"
#      challenging cases: "avian animal" -> "avian food product"
#	   poultry food product
#		 poultry meat food product
#		 chicken food product
#		   chicken meat food product (possibly deprecate)
#		   duck meat food product
#
#	2) link "{organism} food product" as child of "{organism} material" if it
#      exists, otherwise link to first "{parent organism} material" class.
#
# If a given organism does not have a template menu "{organism} material" 
# selected:
# 	1) every template reference to "{organism} material" should instead point
#      to "{parent organism} material"
# should be 
# 	- It is linked and optionally
# 
# If user doesn’t select the generation of an {organism} material parent, then template needs to know what parent material class to reference.  Ideally this is calculated dynamically – by looking for nearest [parent] material where parent is a parent of [organism]; but compilation script doesn’t know this unless it has tool to look it up.
#
# This reads in foodon-edit.owl, merges all imports, then performs a query to fetch the whole organism hierarchy for lookup purposes and to distinguish , and then one to retrieve ALL organism material hierarchy.
#
# EXAMPLES
#
# Retrieve up to depth 4, excluding terms with characteristics alive, raw, dead.
# python3 foodon_table.py -d 4 -e "alive;raw;dead" 
#
# PARAMETERS
# 
# Author: Damion Dooley Nov 2025

import argparse
import csv
import re
import pandas as pd
import io
import subprocess
import sys
# Also relies on command line robot tool: https://robot.obolibrary.org/
import pygtrie # pip install pygtrie

# For owlready2 to not complain: "Warning: SQLite3 version 3.40.0 and 3.41.2 
# have huge performance regressions", Mac users may need to run 
# "> conda install libsqlite --force-reinstall -y"
from owlready2 import * 
import owlready2.sparql.parser
owlready2.sparql.parser._DATA_PROPS = set()

#                      animal              plant by taxonomy   lichen              fungus
SEARCH_ROOT = 'obo:FOODON_03411301,obo:FOODON_00003004,obo:FOODON_03413357,obo:FOODON_03411261'; 
# 00001002: Food product; 03420116: Organism material

INPUT_FOODON_ONTOLOGY = 'cache-foodon-merged.owl';
OBO = "<http://purl.obolibrary.org/obo/";

def init_parser():

	parser = argparse.ArgumentParser(
	    description='This script extracts a table format report of the FoodOn organism, food material and food product hierarchies, with various filters to enable customization towards particular application or database use.',
	    formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument(
		"-r",
		"--root",
		dest="root",
		default=SEARCH_ROOT,
		help="The whole organism node at root of hierarchic query for returning whole organism, material, food product and taxonomy table rows.",
	);

	parser.add_argument(
		"-d",
		"--depth",
		dest="depth",
		type=int,
		help="Include a depth filter to limit hierarchy from given root terms down to this depth.",
	);

	parser.add_argument(
		"-e",
		"--exclude",
		dest="exclude",
		default='',
		help='A comma separated list of term labels or identifiers which are either food material or characteristic classes, like "--exclude "invertebrate animal|live|dead|raw|frozen|cooked", etc. If a food material is or has one of these terms, it will be EXCLUDED from report. \n\nWhen a term is excluded, such as "animal carcass", then all its children are too.\n\nSome names are predefined bundles of characteristics: "lifecycle" means include food sources which have a "dead" (carcass) or "alive" characteristic.  ',
	);

	parser.add_argument(
		"-p",
		"--product",
		dest="product",
		default=True,
		action="store_true",
		help='Include "[x] food product" category in report as child of "[x] material" or nearest ancestor.',
	);

	parser.add_argument(
		"-m",
		"--material",
		dest="material",
		default=True,
		action="store_true",
		help='Include "[x] material" in report as parent of given term.',
	);


	parser.add_argument(
		"-x",
		"--dbxrefs",
		dest="dbxrefs",
		default='',
		help="A list of cross-references to include, by prefix (e.g. asfis,eolife,grin,itis,langual,wd(wikidata),wikipedia).",
	)

	parser.add_argument(
		"-f",
		"--fresh",
		dest="fresh",
		default=False,
		action="store_true",
		help="A flag which indicates whether to regenerate the merged reasoned FoodOn ontology (from src/ontology/foodon-edit.ofn) on which this report is based.",
	)

	parser.add_argument('--version', action='version', version='1.0.0');

	return parser.parse_args();

def get_material(focus_id):
	if focus_id in term_id_to_labels:
		label = term_id_to_labels[focus_id];
		lookup_label = label + ' material';
		if lookup_label in term_label_to_ids:
			id = term_label_to_ids[lookup_label][0];
			#return f'[{term_id_to_labels[id]}]';
			return 'm	';

	return '	'; # tab

def get_food_product(focus_id):
	if focus_id in term_id_to_labels:
		label = term_id_to_labels[focus_id];
		lookup_label = label + ' food product';
		if lookup_label in term_label_to_ids:
			id = term_label_to_ids[lookup_label][0];
			#return f'[{term_id_to_labels[id]}]';
			return 'p	';
	return '	'; # tab


def getEnglishLabel(onto_class):

	english_label = '';

	for label in onto_class.label:
		# What about British english etc?
		if hasattr(label, 'lang') and label.lang:
			if (label.lang == 'en'):
				return str(label);
			else: # some other language
				pass
		elif isinstance(label, str) and english_label == '': #
			english_label = label;
		else:
			print("PROBLEM CASE LABEL:",label)

	return english_label;

def update_term_suffix_test(term_attr, label, match_string):
	print("LABEL", label)
	if hasattr(label, 'locale') and label.locale.en and str(label).endswith(match_string):
		term_attr = str(label);
	elif isinstance(label, str) and label.endswith(match_string):
		term_attr = label;
	sys.exit(0)

def fixDatatype():
	# "namespacestring" datatype used on labels.
	class MyDataType(object):
	    def __init__(self, value): self.value = value
	    def __repr__(self): return f"MyDataType({self.value})"
	# Define the parser and unparser functions
	def my_parser(s): return MyDataType(s)
	def my_unparser(x): return str(x.value)
	owlready2.declare_datatype(MyDataType, 'http://www.w3.org/XML/1998/namespacestring', my_parser, my_unparser)

def findClassByName(text):
	found_classes = list(default_world.search(label = text));
	if found_classes:
		return found_classes[0];

	return False;


def parse_owl_namespaces(file_path, lines_number):
	"""
	Reads an OWL ontology file (RDF/XML format) and extracts the 
	namespace prefixes and URIs from the <rdf:RDF> root tag.

	Args:
	    file_path (str): The path to the OWL file.

	Returns:
	    dict: A dictionary where keys are namespace prefixes (or None for 
	          default namespace) and values are URIs.
	"""
	# Read the first 100 lines to ensure we capture the root element
	with open(file_path) as input_file:
		head = [next(input_file) for _ in range(lines_number)]

	namespaces = {};
	for line in head:
		# Match to e.g. xmlns:swrl="http://www.w3.org/2003/11/swrl#"
		stripped = line.strip();
		if stripped.startswith("xmlns:"):
			prefix, uri = stripped[6:].replace('"','').split('=',1);
			namespaces[uri] = prefix;

	return namespaces


###############################################################################
if __name__ == "__main__":

	options = init_parser();

	# Build organism hierarchy:
	# Note: wheras animal has "live animal", "animal carcass", plants are
	# rarely categorized that way - the plant doesn't have a heart that "dies".
	# And while a whole animal is "slaughtered", a plant or mushroom is 
	# "harvested" and that often means severing it from some or all of its
	# root systems, or harvesting parts of a perennial plant. Most often we are
	# dealing with a harvested "piece of plant", or plant substance like sap.
	# 
	# When we say "x material", we mean x is (rather ambiguously) material from
	# an organism having some taxonomy (spelled out in an equivalence axiom).
	#
	# whole plant PO_0000003 anatomical term which also has "'only in taxon' some Viridiplantae"
	# IN:
	#	plant anatomical entity
	#		plant structure
    #			whole plant
    #			plant by taxonomy
    #			multi-tissue plant structure
    #				fruit etc.
    #
    # 	organism material
    #		algal material
    #			algae FOODON_03411301
    #		animal material
    #			animal FOODON_00003004
    #		plant material
    #			whole plant
    #				plant by taxonomy FOODON_03413357
    #		fungus material
    #			fungus FOODON_03411261
    #			mushroom material
    #				mushroom fruitbody 
    #			yeast material
    #				yeast
    #
    #
	# Read the TSV file with headers: ?id	?parent_id	?type	?label

	if options.fresh:
		print("Freshening"); # Ensure latest report is available
		# "robot merge --input ../foodon-edit.ofn reason --reasoner ELK --create-new-ontology true --exclude-duplicate-axioms true relax reduce --output ../foodon-merged.ofn"
		# Note, all boolean switches require a true or false parameter.
		subprocess.check_output(["robot", "merge", "--input", "../foodon-edit.ofn", 'reason', '--reasoner','ELK','--exclude-duplicate-axioms', "relax","--output", INPUT_FOODON_ONTOLOGY]);


	# FIX WIERD CDNO ONTOLOGY PROBLEM where wrong label datatype exists
	fixDatatype();

	onto = get_ontology('file://./' + INPUT_FOODON_ONTOLOGY).load();
	# Generated just to get namespaces:

	obo = get_namespace("http://purl.obolibrary.org/obo/");

	# AS of 2025: Owlready2 can't generate the RDF document's xmlns namespace
	# prefixes and uris. Workaround, read the first 100 lines of 
	# INPUT_FOODON_ONTOLOGY directly and parse 
	# xmlns:go="http://www.geneontology.org/formats/oboInOwl#" etc.
	reverse_namespace_dict = parse_owl_namespaces(INPUT_FOODON_ONTOLOGY, 100);
	# Create a Trie instance
	namespace_trie = pygtrie.CharTrie(**reverse_namespace_dict);

	# 
	#standard_characteristics = "raw;frozen;cooked;precooked;dried;freeze-dried";

	# Prime stack with given ontology term iris.
	stack = [];
	for root_uri in options.root.split(','):
		onto_uri = root_uri.split(':')[1]; # dropping obo: prefix.
		if obo[onto_uri]:
			stack.append({'term': obo[onto_uri], 'depth':0});
		else:
			print ('WARNING: ', onto_uri, ' was not found in ontology');

	filter = set(options.exclude.split(','));
	#print("FILTER:", filter)
	dbxref_set = set(options.dbxrefs.split(','));
	missing_material_links = [];
	missing_product_links = [];

	# Fetch hierarchies of animal / plant by taxonomy / algae / fungus for hierarchic lookup.
	while len(stack):
		# Depth-first search: pops object off of end of stack; for breadth use .pop(0)
		obj = stack.pop();
		parent_depth = obj['depth'];
		item_depth = obj['depth'];
		next_depth = obj['depth']+1;
		onto_class = obj['term'];

		# Limit depth search by given option
		if options.depth and item_depth > options.depth:
			continue;

		term = {
			'uri': str(onto_class),
			'label': '',
			'taxon': '',
			'product': '',
			'material': '',
			'characteristics': set(),
			'dbxrefs': set()
		}

		term['label'] = getEnglishLabel(onto_class);

		found_material = findClassByName(term['label'] + ' material');
		if not found_material:
			# look for ancestor 
			#update_term_suffix_test(term['material'], label, ' material');
			pass

		found_product = findClassByName(term['label'] + ' food product');
		if not found_material:
			#update_term_suffix_test(term['product'], label, ' food product');
			pass

		if found_material:
			term['material'] = 'm';
			# If a [x] material class is found, and this is a subclass of it, 
			if found_material in onto_class.is_a:
				#link = found_material.iri.split('/')[-1];
				link = str(found_material.iri).replace('http://purl.obolibrary.org/obo/','obo:');
				print (link, '', parent_depth, "  " * (parent_depth) + getEnglishLabel(found_material), '', '', '', sep='\t');
				# Bump depth since we now have a material
				item_depth += 1;
				next_depth = item_depth+1;
				# Here we can add found_material's OTHER children to stack.
				# - and take out this child from that list.
				# Other children include "piece of [x]" and "piece(s) of [x]"
				# as well as other hierarchies ...





			else:
				missing_material_links.append(str(term['label']));

		if found_product:
			term['product'] = 'p';
			if found_material and not (found_product in found_material.is_a):
				missing_product_links.append(getEnglishLabel(found_material) + "/" + getEnglishLabel(found_product));

		# Note special case for "edible frog" FOODON_03413463 where divider 
		# line signals "or" condition
		#    str(taxon) = 'obo:NCBITaxon_45623 | obo:NCBITaxon_8406'
		for taxon in onto_class.RO_0002162:
			if term['taxon'] == '':
				term['taxon'] = [];
			term['taxon'].append(str(taxon).replace('.',':') );

		for parent in onto_class.is_a:  # An array.	
			if hasattr(parent, 'label'):			
				for label in parent.label: # an array
					pass

		# Each prop is an object with [onto_class] as a key pointing to an
		# array of values (for >1 prop relation)
		# .IAO_0000114 has curation status; .image ;.hasDbXref .label 
		# .IAO_0000119 definition source .IAO_0000115 definition .contributor
		# .comment; .hasExactSynonym .date

		for prop in onto_class.get_class_properties(): 
			for value in prop[onto_class]: # value in an array.
				match prop.python_name:
					#case 'label': # With locale?
					#case 'RO_0002162': # in taxon
					# Echo selected dbxrefs out into a column dedicated to that
					case 'hasDbXref':

						if options.dbxrefs:
							value = str(value);
							found = '*' in dbxref_set;
							# The .longest_prefix() method returns a tuple of (key, value)
							match = namespace_trie.longest_prefix(value); 
							if match:
								code, prefix = match
								if prefix in dbxref_set:
									found = True;
								value = prefix + ':' + value[len(code):];
							elif not found and value.partition(':')[0] in dbxref_set:
								found = True;
							if found:
								term['dbxrefs'].add(value);

					case 'RO_0000086' | 'RO_0000053':
						#text = str(value).replace('.',':'); # value is 
						char_label = getEnglishLabel(value);
						term['characteristics'].add(char_label); # Could add uri too?
					case _:
						#print("UNRECOGNIZED", prop.python_name)
						pass

		if term['characteristics']:
			# apply filter to characteristics if any
			characteristics = ';'.join(term['characteristics']);
		else:
			characteristics = '';

		if term['dbxrefs']:
			dbxrefs = ';'.join(term['dbxrefs']);
		else:
			dbxrefs = '';
				
		# Set intersection: this class is a filtered item, or has a filtered
		# characteristic so ignore it.
		if (term['label'] in filter) or (term['characteristics'] & filter):
			#print ("FILTERING", term['label'], term['characteristics'] & filter)
			continue;

		#if (not lifecycle) or options.lifecycle:
		# Usually only 1 taxon term but some terms like "edible frog" have 2+
		print (term['uri'].replace('.',':'), term['material'] + term['product'], item_depth, "  " * item_depth + term['label'], ';'.join(term['taxon']), characteristics, dbxrefs, sep='\t');

		# Sort children alphabetically
		children = sorted(onto_class.subclasses(), key=lambda term: getEnglishLabel(term), reverse=True);
		for subclass in children:
			stack.append({'term': subclass, 'depth': next_depth});

	# 1-to-many lookup of term to children and visa vesa.
	#item_children = df.groupby('parent_id')['id'].apply(list).to_dict();
	#item_parents = df.groupby('id')['parent_id'].apply(list).to_dict();
	if missing_material_links:
		print("\nMATERIAL CLASS link to whole organism MISSING for:\n ", missing_material_links);

	if missing_product_links:
		print("\nMATERIAL CLASS link to product MISSING for:\n ", missing_product_links , '\n');

# 2nd pass could create lookup function for material and product hierarchy for each term in table.
# add info to each row for material and product hierarchy if not yet ascertained.
# 
