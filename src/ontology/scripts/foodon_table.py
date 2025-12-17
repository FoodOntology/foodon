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
# PARAMETERS
# 
# Author: Damion Dooley Nov 2025

import optparse
import csv
import re
import pandas as pd
import io
import subprocess
import sys
# Also relies on command line robot tool: https://robot.obolibrary.org/

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
RE_LOCALE = r'(?P<label>[^@]+)(?P<locale>\@[a-zA-Z-]*)?';

def init_parser():
	parser = optparse.OptionParser();

	parser.add_option(
		"-r",
		"--root",
		dest="root",
		default=SEARCH_ROOT,
		help="The whole organism node at root of hierarchic query for returning whole organism, material, food product and taxonomy table rows.",
	);

	parser.add_option(
		"-l",
		"--lifecycle",
		dest="lifecycle",
		action="store_true",
		help="Include live organism and organism carcass terms",
	);

	parser.add_option(
		"-d",
		"--depth",
		dest="depth",
		type=int,
		help="Include a depth filter to limit hierarchy from given root terms down to this depth.",
	);

	parser.add_option(
		"-c",
		"--characteristic",
		dest="characteristic",
		help='A vertical bar | separated list of characteristics like "-c raw|frozen|cooked|shell on", etc. If a food material has one, it will be included in report.',
	);

	parser.add_option(
		"-p",
		"--product",
		dest="product",
		help='include in report.',
	);


	parser.add_option(
		"-f",
		"--fresh",
		dest="fresh",
		default=False,
		action="store_true",
		help="A flag which indicates whether to regenerate the merged reasoned FoodOn ontology on which this report is based.",
	)

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

def update_term_suffix_test(term_attr, label, match_string):
	print("LABEL", label)
	if hasattr(label, 'locale') and label.locale.en and str(label).endswith(match_string):
		term_attr = str(label);
	elif isinstance(label, str) and label.endswith(match_string):
		term_attr = label;
	sys.exit(0)

if __name__ == "__main__":

	options, args = init_parser();

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
		subprocess.check_output(["robot", "merge", "--input", "../foodon-edit.ofn", 'reason', '--reasoner','ELK','--exclude-duplicate-axioms', "relax", "reduce","--output", INPUT_FOODON_ONTOLOGY]);

	# THIS SECTION IS TO FIX WIERD CDNO ONTOLOGY PROBLEM where wrong 
	# "namespacestring" datatype used on labels.
	class MyDataType(object):
	    def __init__(self, value): self.value = value
	    def __repr__(self): return f"MyDataType({self.value})"
	# Define the parser and unparser functions
	def my_parser(s): return MyDataType(s)
	def my_unparser(x): return str(x.value)
	owlready2.declare_datatype(MyDataType, 'http://www.w3.org/XML/1998/namespacestring', my_parser, my_unparser)

	onto = get_ontology('file://./' + INPUT_FOODON_ONTOLOGY).load();
	obo = get_namespace("http://purl.obolibrary.org/obo/");

	# The bracketed expressions for characteristics need to be deteted so that they can be filtered out if desired.
	standard_characteristics = "raw|frozen|cooked|precooked|dried|freeze-dried|shell on|shell off";

	stack = [];
	for root_uri in options.root.split(','):
		onto_uri = root_uri.split(':')[1]; # dropping obo: prefix.
		if obo[onto_uri]:
			stack.append({'term': obo[onto_uri], 'depth':0});
		else:
			print ('WARNING: ', onto_uri, ' was not found in ontology');

	# Fetch hierarchies of animal / plant by taxonomy / algae / fungus for hierarchic lookup.
	while len(stack):
		# Depth-first search: pops object off of end of stack; for breadth use .pop(0)
		obj = stack.pop();
		depth = obj['depth'];
		onto_class = obj['term'];

		# Limit depth search by given option
		if options.depth and depth > options.depth:
			continue;

		term = {
			'uri': str(onto_class),
			'label': '',
			'taxon': '',
			'product': '',
			'material': '',
			'characteristics': ''
		}

		for label in onto_class.label:
			if hasattr(label, 'locale') and label.locale.en:
				term['label'] = str(label);
			elif isinstance(label, str) and term['label'] == '':
				term['label'] = label;

		found_classes = list(default_world.search(label = term['label'] + ' material'))
		if found_classes:
			term['material'] = 'm';
		else:
			# look for ancestor 
			#update_term_suffix_test(term['material'], label, ' material');
			pass

		found_classes = list(default_world.search(label = term['label'] + ' food product'))
		if found_classes:
			term['product'] = 'p';
		else:
			#update_term_suffix_test(term['product'], label, ' food product');
			pass

		for taxon in onto_class.RO_0002162:
			term['taxon'] = str(taxon).split('.')[1];

		lifecycle = False;

		for parent in onto_class.is_a:  # An array.	
			if hasattr(parent, 'label'):			
				for label in parent.label: # an array
					pass

		for subclass in onto_class.subclasses():
			stack.append({'term': subclass, 'depth': depth + 1});


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

					case 'RO_0000086':
						if term['characteristics'] == '':
							term['characteristics'] = {};
						text = str(value).split('.')[1];
						term['characteristics'][text] = True;
						if text == 'PATO_0001421' or text == 'PATO_0001422': # LIVE or DEAD
							lifecycle = True;

					case 'RO_0000053': # has quality
						if term['characteristics'] == '':
							term['characteristics'] = {};
						text = str(value).split('.')[1];
						term['characteristics'][text] = True;
						if text == 'PATO_0001421' or text == 'PATO_0001422': # LIVE or DEAD
							lifecycle = True;

					case _:
						#print("ONE", prop.python_name)
						pass

		if term['characteristics']:
			characteristics = '(' + ','.join(term['characteristics']) + ')';
		else:
			characteristics = '';

		if (not lifecycle) or options.lifecycle:
			print (term['uri'].split('.')[1], term['material'], term['product'], "  " * depth + term['label'], term['taxon'], characteristics, sep='\t')

	# 1-to-many lookup of term to children and visa vesa.
	#item_children = df.groupby('parent_id')['id'].apply(list).to_dict();
	#item_parents = df.groupby('id')['parent_id'].apply(list).to_dict();

	# A term/item may have multiple labels, some with or without language modifier
	term_id_to_labels = {};
	# Label to item lookup, understanding that there may be duplicate labels.
	term_label_to_ids = {}; 

