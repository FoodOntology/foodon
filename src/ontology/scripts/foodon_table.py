# foodon_table.py
#
# This script generates a user-defined table report of FoodOn contents which is
# oriented to providing clear menus for whole organism, organism material,
# food product and food process branches of the ontology.  It uses the 
# Owlready2 python library for reading and querying the structure of an OWL RDF
# graph.
# 
# By default the script will base its hierarchic tree on what we usually want:
# Extract one mono-hierarchy of whole animal or plant or fungi references, (as 
# well as a hierarchy of Foodon's food processes which are used in recipes). 
# This structure enables nearest parent/ neighbour lookup for each organism to
# its "[organism] material" or "[organism] food product" parent. We need 
# separate animal / plant/ algae / fungus hierarchy in order to trace animal
# parenthood clearly.
#
# 	- "animal" FOODON_00003004 (whole organism, common name)
#      Don't want this: - people should manually construct the merger if they
#	   need it - but currently it has hardcoded children to move:
#	    - "fish or lower water animal" FOODON_03411021
#
#      - Issue: cow food product vs beef food product?
#	
#	- "whole plant"
#	   - "plant by taxonomy" FOODON_03413357 (common name)
#		 	- handle "brassica species" etc.
# 	- "algae" FOODON_03411301
#	- "fungus" FOODON_03411261
#
# If a whole organism class has a corresponding "[organism] food product" class
# (which can be created by curators using the organism template menu):
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
# 
# Note: wheras animal has "live animal", "animal carcass", plants are
# rarely categorized that way - the plant doesn't have a heart that "dies".
# And while a whole animal is "slaughtered", a plant or mushroom is 
# "harvested" and that often means severing it from some or all of its
# root systems, or harvesting parts of a perennial plant. Most often we are
# dealing with a harvested "piece of plant", or plant substance like sap.
#	
# If user doesn’t select the generation of an {organism} material parent, then
# template needs to know what parent material class to reference.  Ideally this
# is calculated dynamically – by looking for nearest [parent] material where
# parent is a parent of [organism]; but compilation script doesn’t know this
# unless it has tool to look it up.
#
# This script operates in /src/ontology/scripts/ folder, and usese a 
# cache-foodon-merged.owl file which is the merger of foodon-edit.ofn and all
# its imports.  To regenerate this cacehd file, add the -f --freshen parameter
# to foodon_table.py. The script performs a query to fetch the organisms or
# other branches given on command line using the -r --root parameter.
#
# EXAMPLES
#
# Retrieve up to depth 4, excluding terms with characteristics alive, raw, dead
# and include langual mapping.  NOTE exclusion delimiter is SEMICOLON since 
# some characteristics might have commas in them
#
# python3 foodon_table.py -d 4 -e "alive;raw;dead" -x langual
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
from collections import deque

# For owlready2 to not complain: "Warning: SQLite3 version 3.40.0 and 3.41.2 
# have huge performance regressions", Mac users may need to run 
# "> conda install libsqlite --force-reinstall -y"
from owlready2 import * 
import owlready2.sparql.parser
owlready2.sparql.parser._DATA_PROPS = set()

# organism material: FOODON_03420116

# animal:FOODON_00003004, plant by taxonomy:FOODON_03413357
# algae: FOODON_03411301, fungus:FOODON_03411261, lichen:FOODON_03412345
# food product: 00001002; 
# completely executed planned process: COB_0000035
SEARCH_ROOT = 'obo:FOODON_00003004,obo:FOODON_03413357,obo:FOODON_03411301,obo:FOODON_03411261,lichen:FOODON_03412345,obo:COB_0000035';

INPUT_FOODON_ONTOLOGY = 'cache-foodon-merged.owl';
OBO = "<http://purl.obolibrary.org/obo/";
OBO_URI_BASE = 'http://purl.obolibrary.org/obo/';

DBXREF_URL_TEMPLATES = {
	'langual':   'http://www.langual.org/langual_thesaurus.asp?termID={}',
	'wd':        'https://www.wikidata.org/wiki/{}',
	'wikipedia': 'https://en.wikipedia.org/wiki/{}',
	'itis':      'https://www.itis.gov/servlet/SingleRpt/SingleRpt?search_topic=TSN&search_value={}',
	'grin':      'https://npgsweb.ars-grin.gov/gringlobal/taxonomydetail.aspx?id={}',
	'eolife':    'https://eol.org/pages/{}',
};

def md_linkify(cell):
	"""Convert semicolon-separated obo: URIs or known dbxref values to markdown links."""
	if not cell:
		return cell;
	result = [];
	for part in cell.split(';'):
		part = part.strip();
		if part.startswith('obo:'):
			local = part[4:];
			result.append(f'[{local}]({OBO_URI_BASE}{local})');
		else:
			sep = part.find(':');
			if sep > 0:
				prefix, value = part[:sep], part[sep+1:];
				if prefix in DBXREF_URL_TEMPLATES:
					result.append(f'[{part}]({DBXREF_URL_TEMPLATES[prefix].format(value)})');
				else:
					result.append(part);
			else:
				result.append(part);
	return '; '.join(result);

def init_parser():

	parser = argparse.ArgumentParser(
	    description='This script extracts a table format report of the FoodOn organism, food material and food product hierarchies, with various filters to enable customization towards particular application or database use.',
	    formatter_class=argparse.RawDescriptionHelpFormatter
	);

	parser.add_argument(
		"-r",
		"--root",
		dest="root",
		default=SEARCH_ROOT,
		help="The whole organism node at root of hierarchic query for returning whole organism, material, food product and taxonomy table rows.",
	);

	# Obsolete.  To omit processes, just run with "-r [onto_id,...] where only organisms supplied.
	#parser.add_argument(
	#	"-P",
	#	"--process",
	#	dest="process",
	#	default=True,
	#	action="store_true",
	#	help='Include process terms. Formally, this includes all FoodOn processes under the "Completely executed planned process" branch. (So named to convey that each term\'s parts and axioms are guaranteed to hold, unlike a failed process where any number of properties may be missing.)'
	#);

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
		help='A semicolon-separated list of term labels or characteristic labels to exclude. Multiple values must be quoted on the command line, e.g. -e "alive;raw;dead". If a term\'s label matches, or if the term has a matching characteristic, it and all its children are excluded from the report.'
	);

	parser.add_argument(
		"-p",
		"--product",
		dest="product",
		default=False,
		action="store_true",
		help='Include food product hierarchy. This displays a "[x] food product" category in report as child of "[x] material" or nearest ancestor.'
	);

	parser.add_argument(
		"-m",
		"--material",
		dest="material",
		default=False,
		action="store_true",
		help='Include "[x] material" in report as parent of given term.'
	);


	parser.add_argument(
		"-x",
		"--dbxrefs",
		dest="dbxrefs",
		default='',
		help="A list of cross-references to include, by prefix (e.g. asfis,eolife,grin,itis,langual,wd,wikipedia). wd=wikidata for cultivar names."
	);

	parser.add_argument(
		"-M",
		"--markdown",
		dest="markdown",
		default=False,
		action="store_true",
		help="Output a markdown table instead of tab-delimited text."
	);


	parser.add_argument(
		"-D",
		"--definition",
		dest="definition",
		default=False,
		action="store_true",
		help="Include a definition column populated from the IAO:0000115 annotation."
	);

	parser.add_argument(
		"-s",
		"--synonyms",
		dest="synonyms",
		default=False,
		action="store_true",
		help="Include a column for a list of synonyms (hasSynonym and hasExactSynonym). This is helpful for text-mining applications."
	);

	parser.add_argument(
		"-f",
		"--fresh",
		dest="fresh",
		default=False,
		action="store_true",
		help="A flag which indicates whether to regenerate the merged reasoned FoodOn ontology (from src/ontology/foodon-edit.ofn) on which this report is based."
	);

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
	#if not 'label' in onto_class:
	#	return "NO LABEL";

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

# 
def setProperties(term, onto_class):
	
	for prop in onto_class.get_class_properties(): 
		for value in prop[onto_class]: # value in an array.
			match prop.python_name:
				#case 'label': # handled separately
				#case 'RO_0002162': # in taxon
				# Echo dbxrefs out by selected prefix or *=all
				# DBXREFS
				case 'hasDbXref':
					if options.dbxrefs:
						value = str(value);
						found = '*' in dbxref_filter;
						# match given URI to longest prefix
						match = namespace_trie.longest_prefix(value); 
						if match:
							code, prefix = match
							if prefix in dbxref_filter:
								found = True;
							value = prefix + ':' + value[len(code):];
						elif not found and value.partition(':')[0] in dbxref_filter:
							found = True;
						if found:
							term['dbxrefs'].add(value);
				# DEFINITION
				case 'IAO_0000115':
					if options.definition and not term['definition']:
						term['definition'] = str(value);
				# CHARACTERISTICS
				case 'RO_0000086' | 'RO_0000053':
					#text = str(value).replace('.',':'); # value is 
					char_label = getEnglishLabel(value);
					term['characteristics'].add(char_label); # Could add uri too?
				case '':
					pass
				case _:
					#print("UNRECOGNIZED", prop.python_name)
					pass


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

def display(link, depth, term, mat_prod_code='', taxonomy='', characteristics='', dbxrefs='', synonyms='', definition=''):
	output_buffer.append({
		'id':              link,
		'flags':           mat_prod_code,
		'depth':           depth,
		'label':           getEnglishLabel(term),
		'taxonomy':        taxonomy,
		'characteristics': characteristics,
		'dbxrefs':         dbxrefs,
		'synonyms':        synonyms,
		'definition':      definition,
	})

###############################################################################
if __name__ == "__main__":

	options = init_parser();

	# Build organism hierarchy:
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

	if options.fresh:
		print("Freshening"); # Ensure latest report is available
		# "robot merge --input ../foodon-edit.ofn reason --reasoner ELK --create-new-ontology true --exclude-duplicate-axioms true relax reduce --output ../foodon-merged.ofn"
		# Note, all boolean switches require a true or false parameter.
		# NOTE: Using .owl output format because due to robot issue, only this
		# preserves xmlns: prefixes.
		subprocess.check_output(["robot", "merge", "--input", "../foodon-edit.ofn", 'reason', '--reasoner','ELK','--exclude-duplicate-axioms', "relax","--output", INPUT_FOODON_ONTOLOGY]);
		#subprocess.check_output(["robot", "merge", "--input", "../foodon-edit.ofn", "--output", INPUT_FOODON_ONTOLOGY]);

	# FIX CDNO ONTOLOGY PROBLEM where wrong label datatype exists
	fixDatatype();

	try:
		onto = get_ontology('file://./' + INPUT_FOODON_ONTOLOGY).load();
	except Exception:
		print (f'Unable to load "{INPUT_FOODON_ONTOLOGY}"". To generate this cached file in the /scripts/ folder, add the -f --freshen parameter, which will read foodon-edit.ofn and generate a compiled, reasoned version.');
		sys.exit(1);

	# Generated just to get obo. prefixed namespaces:
	obo = get_namespace("http://purl.obolibrary.org/obo/");

	# AS of Nov 2025: Owlready2 can't generate the RDF document's xmlns namespace
	# prefixes and uris. Workaround, read the first 100 lines of 
	# INPUT_FOODON_ONTOLOGY directly and parse 
	# xmlns:go="http://www.geneontology.org/formats/oboInOwl#" etc.
	reverse_namespace_dict = parse_owl_namespaces(INPUT_FOODON_ONTOLOGY, 100);
	# Create a Trie instance
	namespace_trie = pygtrie.CharTrie(**reverse_namespace_dict);

	# Prime stack with given ontology term iris.
	stack = deque();
	for root_uri in options.root.split(','):
		onto_uri = root_uri.split(':')[1]; # dropping obo: prefix.
		if obo[onto_uri]:
			stack.append({'term': obo[onto_uri], 'depth':0});
		else:
			print ('WARNING: root parameter ', onto_uri, ' was not found in ontology');

	term_filter = set(filter(None, options.exclude.split(';')));
	#print("FILTER", term_filter)

	dbxref_filter = set(options.dbxrefs.split(','));
	missing_material_links = [];
	missing_product_links = [];

	processed = set();

	output_buffer = []

	# Fetch hierarchies of animal / plant by taxonomy / algae / fungus for hierarchic lookup.
	while len(stack):
		# Depth-first search:
		obj = stack.popleft();
		onto_class = obj['term'];
		if onto_class in processed: # prevent a term from being processed twice.
			continue;

		processed.add(onto_class);
		parent_depth = obj['depth'];
		item_depth = obj['depth'];
		next_depth = obj['depth']+1;

		# Limit depth search by given option
		if options.depth is not None and item_depth > options.depth:
			continue;

		term = {
			'uri': str(onto_class),
			'label': '',
			'taxon': '',
			'product': '',
			'material': '',
			'characteristics': set(),
			'dbxrefs': set(),
			'synonyms': set(),
			'definition': ''
		}

		term['label'] = getEnglishLabel(onto_class);

		found_material = findClassByName(term['label'] + ' material');
		if not found_material:
			# look for ancestor material (ultimately, its already under this)
			#update_term_suffix_test(term['material'], label, ' material');
			pass

		found_product = findClassByName(term['label'] + ' food product');
		if not found_material:
			#update_term_suffix_test(term['product'], label, ' food product');
			pass

		if found_material:
			term['material'] = 'm';
			# If a [x] material class is found, and this is a subclass of it, display it
			# onto_class.is_a provides INFERRED parentood. .get_parents_of is immediate parent(s)
			if not (found_material in processed):
				if found_material in onto.get_parents_of(onto_class):
					if options.material:
						# Print out material_entity line
						link = str(found_material.iri).replace('http://purl.obolibrary.org/obo/','obo:');
						display (link, parent_depth, found_material);

						# Bump depth since we now have a material
						item_depth += 1;
						next_depth = item_depth+1;
						# Here we can add found_material's OTHER children to stack.
						# - and take out this child from that list.
						# Other children include "piece of [x]" and "piece(s) of [x]"
						# as well as other hierarchies ...

				else:
					missing_material_links.append([onto_class, found_material]);

		if found_product:
			term['product'] = 'p';
			if found_material and not (found_material in onto.get_parents_of(found_product)):

				missing_product_links.append([found_product, found_material]);

		# Note special case for "edible frog" FOODON_03413463 where divider 
		# line signals "or" condition
		#    str(taxon) = 'obo:NCBITaxon_45623 | obo:NCBITaxon_8406'
		for taxon in onto_class.RO_0002162:
			if term['taxon'] == '':
				term['taxon'] = [];
			term['taxon'].append(str(taxon).replace('.',':') );

		if options.synonyms:
			for synonym in onto_class.hasSynonym:
				term['synonyms'].add(synonym);
				pass
			for synonym in onto_class.hasExactSynonym:
				term['synonyms'].add(synonym);

		for parent in onto_class.is_a:  # An array.	
			if hasattr(parent, 'label'):			
				for label in parent.label: # an array
					pass

		# Each prop is an object with [onto_class] as a key pointing to an
		# array of values (for >1 prop relation)
		# .IAO_0000114 has curation status; .image ;.hasDbXref .label 
		# .IAO_0000119 definition source .IAO_0000115 definition .contributor
		# .comment; .hasExactSynonym .date

		setProperties(term, onto_class);
				
		# Set intersection: this class is a filtered item, or has a filtered
		# characteristic so ignore it.
		if (term['label'] in term_filter) or (term['characteristics'] & term_filter):
			#print ("FILTERING", term['label'], term['characteristics'] & term_filter)
			continue;

		characteristics = ';'.join(term['characteristics']);
		dbxrefs = ';'.join(term['dbxrefs']);
		synonyms = ';'.join(term['synonyms']);

		# Product class is at same level as whole organism.
		if options.product == True and found_product and not found_product in processed:
			#display (link, item_depth, found_product);
			product_children = sorted(found_product.subclasses(), key=lambda term: getEnglishLabel(term),reverse=True);
			for child in product_children:
				if not (child in processed): # This includes current onto_class
					stack.appendleft({'term': child, 'depth': item_depth});
		
		# If found_material and options.material, then include material's
		# children except for onto_class itself and children which are food
		# products
		if options.material == True and found_material and not found_material in processed:
			material_children = sorted(found_material.subclasses(), key=lambda term: getEnglishLabel(term),reverse=True);
			for child in material_children:
				# processed includes current onto_class;ALSO BLOCK X food product,
				# which is added if options.product is true below.
				if not child in processed and (not (getEnglishLabel(child).endswith(' food product')) or options.product == True): 
					stack.appendleft({'term': child, 'depth': item_depth});

		# Sort this item's children alphabetically
		children = sorted(onto_class.subclasses(), key=lambda term: getEnglishLabel(term),reverse=True);
		for subclass in children: # These get done before any material or food product siblings. parents.
			# If already somehow processed, do we just print a "see also" link?
			stack.appendleft({'term': subclass, 'depth': next_depth});

		# If found_product and options.product, then include product
		# children (product already done above). By doing these separate
		# from food material, they can be a stand-alone option

		# Usually only 1 taxon term but some terms like "edible frog" have 2+
		display (
			term['uri'].replace('.',':'),
			item_depth,
			onto_class,
			term['material'] + term['product'],
			';'.join(term['taxon']),
			characteristics,
			dbxrefs,
			synonyms,
			term['definition']
		);


	COLUMNS = ['id', 'flags', 'depth', 'label', 'taxonomy', 'characteristics', 'dbxrefs', 'synonyms', 'definition']
	ALWAYS_SHOW = {'id', 'depth', 'label'}
	active_cols = [c for c in COLUMNS if c in ALWAYS_SHOW or any(row[c] for row in output_buffer)]

	if options.markdown:
		max_label_width = max((row['depth'] * 2 + len(row['label']) for row in output_buffer), default=5)
		max_label_width = max(max_label_width, 5)
		def esc(s): return str(s).replace('|', '\\|')
		header_cells = ['label' + '&nbsp;' * (max_label_width - 5) if c == 'label' else c for c in active_cols]
		sep_cells    = ['-' * (max_label_width + 2) if c == 'label' else '-' * (len(c) + 2) for c in active_cols]
		print('| ' + ' | '.join(header_cells) + ' |')
		print('|' + '|'.join(sep_cells) + '|')
		for row in output_buffer:
			cells = []
			for col in active_cols:
				if col == 'id':
					cells.append(md_linkify(row['id']))
				elif col == 'depth':
					cells.append(str(row['depth']))
				elif col == 'label':
					cells.append(esc('&nbsp;' * (row['depth'] * 2) + row['label']))
				elif col in ('taxonomy', 'dbxrefs'):
					cells.append(md_linkify(row[col]))
				else:
					cells.append(esc(row[col]))
			print('| ' + ' | '.join(cells) + ' |')
	else:
		print('\t'.join(active_cols))
		for row in output_buffer:
			cells = []
			for col in active_cols:
				if col == 'label':
					cells.append('  ' * row['depth'] + row['label'])
				else:
					cells.append(str(row[col]))
			print('\t'.join(cells))

	# 1-to-many lookup of term to children and visa vesa.
	#item_children = df.groupby('parent_id')['id'].apply(list).to_dict();
	#item_parents = df.groupby('id')['parent_id'].apply(list).to_dict();
	if options.material and missing_material_links:
		print("\n [x] whole organism to [x] material parent MISSING:", len(missing_material_links), '\n');
		# e.g. SubClassOf(obo:AGRO_00002071 obo:COB_0000035)
		for binding in missing_material_links:
			print ("SubClassOf(",str(binding[0]).replace('.',':'), str(binding[1]).replace('.',':'),")");

	if options.product and missing_product_links:
		print("\n [x] food product to [x] material parent MISSING:", len(missing_product_links), '\n');
		for binding in missing_product_links:
			print ("SubClassOf(",str(binding[0]).replace('.',':'), str(binding[1]).replace('.',':'),")");

# 2nd pass could create lookup function for material and product hierarchy for each term in table.
# add info to each row for material and product hierarchy if not yet ascertained.
# 
