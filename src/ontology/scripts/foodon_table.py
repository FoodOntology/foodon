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
#   - "plant by taxonomy" FOODON_03413357 (common name)
#		- Issue: we don't want ""
#		- handle "brassica species" etc.
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

TABLE_FILE = 'foodon_table.tsv';
OBO = "<http://purl.obolibrary.org/obo/";
RE_LOCALE = r'(?P<label>[^@]+)(?P<locale>\@[a-zA-Z-]*)?';

def init_parser():
	parser = optparse.OptionParser();

	parser.add_option(
		"-r",
		"--root",
		dest="root",
		default="FOODON_00003004",
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
		help="Include a depth filter to limit hierarchy down to this depth.",
	);

	parser.add_option(
		"-c",
		"--characteristic",
		dest="characteristic",
		help='A vertical bar | separated list of characteristics like "-c raw|frozen|cooked|shell on", etc. If a food material has one, it will be included in report.',
	);

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
	df = pd.read_csv(TABLE_FILE, sep='\t');

	# Simplify label names
	df.rename(columns={'?id': 'id', '?parent_id': 'parent_id', '?type': 'type', '?label': 'label'}, inplace=True)

	# Remove the OBO URI from id and parent_id columns
	df['id'] = df['id'].str.removeprefix(OBO).str.removesuffix('>');
	df['parent_id'] = df['parent_id'].str.removeprefix(OBO).str.removesuffix('>');

	# 1-to-many lookup of term to children and visa vesa.
	item_children = df.groupby('parent_id')['id'].apply(list).to_dict();
	item_parents = df.groupby('id')['parent_id'].apply(list).to_dict();

	# A term/item may have multiple labels, some with or without language modifier
	term_id_to_labels = {};
	# Label to item lookup, understanding that there may be duplicate labels.
	term_label_to_ids = {}; 

	# Get previous template names and versions:
	# The type and label columns go together 
	for index, row in df.iterrows():
		id = row['id'];
		if id.startswith('FOODON_'): # Also NCBITaxon
			match row['type']:
				case 'label':
					# We favour the @en lable, and the shortest label.
					match = re.search(RE_LOCALE, row['label']);
					if match:
						new_label = match.group('label');
						if not id in term_id_to_labels:
							term_id_to_labels[id] = new_label;

						locale = match.group('locale') or '';
						# Ensure there is a locale-coded lookup
						if locale:
							term_id_to_labels[id + locale] = new_label; #

						# If new label is shorter than existing label, and locale is present
						if (len(term_id_to_labels[id]) > len(new_label)) and locale and locale == '@en':
							term_id_to_labels[id] = new_label;


						if new_label in term_label_to_ids:
							term_label_to_ids[new_label].append(id);
						else:
							term_label_to_ids[new_label] = [id]; # dictionary with id as key

				case 'synonym':
					# Nothing to do here currently.
					pass

				case 'taxon':
					# An item should have an 'in taxon' link at "x material" level if possible.
					pass

	# Now for each animal find "x food product ", or nearest animal parent y's "y food product"
	stack = [{options.root:0}];

	# The bracketed expressions for characteristics need to be deteted so that they can be filtered out if desired.
	#standard_characteristics = "raw|frozen|cooked|precooked|dried|freeze-dried|shell on|shell off";

	if (options.characteristic):
		re_characteristic = "(\(| )({})(\)|,)".format(options.characteristic); # the {} gets substituted.

	print ("REGEX", re_characteristic, options.characteristic);

	while len(stack):
		# Depth-first search: pops object off of end of stack; for breadth use .pop(0)
		binding = stack.pop();
		focus_id, depth = next(iter(binding.items())); 

		# Limit depth search by given option
		if options.depth and depth > options.depth:
			continue;

		if not focus_id.startswith('FOODON_'):
			continue;

		label = term_id_to_labels[focus_id];

		lifecycle = label.startswith('live') or label.endswith('carcass') or label.endswith('carcass (raw)');
		# characteristics = 

		if (not lifecycle) or options.lifecycle:
			# Can only do this by first accepting "[organism] (taxon qualifier)" parenthetical expressions.
			# if options.characteristic:

			#	print ("CHARS", options.characteristic, label, re.search(re_characteristic, label))

			# Include characteristics in parentheses like "raw", "cooked", "frozen", "shell on",
			#if (not options.characteristic) or re.search(re_characteristic, label):

			if focus_id in item_children:
				children = item_children[focus_id];
				if children:
					merged = [{key: value} for key, value in zip(children, [depth+1] * len(children))]
					stack.extend(merged);

			if focus_id in term_id_to_labels:
				# label = term_id_to_labels[focus_id];
				print(focus_id, get_material(focus_id), get_food_product(focus_id), depth, "  " * depth + label);


			# NCBITaxon:
			#else:
			#	print(focus_id, (" " * depth) + 'n/a');

			# Add label and parenthood options.  
			# Some items have two labels - e.g. BFO_0000024 "fiat object" "fiat object part"
			# 
			


	"""
	if os.path.isfile(DH_TEMPLATES_FILENAME):
		# create a Pandas "df" dataframe
		df = pd.read_excel(
			DH_TEMPLATES_FILENAME, 
			sheet_name = DH_TEMPLATES_TAB_RELEASES # Load by sheet name
		) 
		# Note: if a column is blank or not a number, pandas by default returns
		# it as nan.
		# Set all the columns from this Releases tab as datatype (id) str
		# might help in some cases but we seemed to have to detect nan anyways
		# so not implementing the parameter below
		#dtype = dict.fromkeys(DH_TEMPLATE_VERSION_CONTROL_FIELDS, str)

		#print("Loaded file", df);
		process_release(df);

	else:
		print(f"Please generate the {DH_TEMPLATES_FILENAME} filename by including the --download parameter.");
	"""
