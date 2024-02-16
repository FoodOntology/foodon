#!/usr/bin/env python3
"""
Author : Kai Blumberg https://orcid.org/0000-0002-3410-4655
Date   : 2022-11-28
Purpose: Generate an organism parts robot template from an input template file, species list and organism module


run: ./organism_parts.py -t input_templates/12.tsv -i species_list.tsv -o output_templates/test_output.tsv -m input_ontology/id_mapping_table.tsv -n results/mapping_table_output.tsv -org animal -id input_ontology/id_integer.txt -c 'https://orcid.org/0000-0001-5275-8866|https://orcid.org/0000-0002-8844-9165|https://orcid.org/0000-0002-3410-4655|https://orcid.org/0000-0002-3410-4655'

"""

import argparse
import sys
import csv
import datetime
from collections import namedtuple


# --------------------------------------------------
def get_args():
    """get command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Argparse Python script',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        '-t',
        '--template',
        help='input template tsv file',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-i',
        '--input',
        help='input species list tsv file',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-o',
        '--output',
        help='output file path',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-m',
        '--mapping',
        help='input tsv file for ID mapping lookup table',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-n',
        '--new_terms',
        help='output tsv file of new terms to add to ID mapping table',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-org',
        '--organism',
        help='Module to run e.g., animal or plant',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-id',
        '--identifier',
        help='File with latest ID integer for the module',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-c',
        '--contributor',
        help='String of | delimited term contributor ORIDs without spaces e.g. "https://orcid-866|https://orcid-165"',
        metavar='str',
        type=str,
        default='')

    return parser.parse_args()


# --------------------------------------------------
def warn(msg):
    """Print a message to STDERR"""
    print(msg, file=sys.stderr)


# --------------------------------------------------
def die(msg='Something bad happened'):
    """warn() and exit with error"""
    warn(msg)
    sys.exit(1)


# --------------------------------------------------
def assign_ID(input_mapping_list, Sheet_ID,id_int):
    res = next((item for item in input_mapping_list if item["Sheet_ID"] == Sheet_ID), None)
    new_id = True
    if res:
        new_id = False
        FOODON_ID = res['FOODON_ID']
        created = res['created']
        contributor = res['contributor']
        see_also = res['see_also']
        exact_syn = res['exact_syn']
        broad_syn = res['broad_syn']
        narrow_syn = res['narrow_syn']
        database_cross_reference = res['database_cross_reference']
        comment = res['comment']
        editor_note = res['editor_note']
        imported_from = res['imported_from']
        definition_source = res['definition_source']
        image = res['image']
        image_rights_holder = res['image_rights_holder']
        image_license = res['image_license']

    else:
        if id_int < 10:
            FOODON_ID = 'FOODON:0000000{}'.format(id_int)
        elif id_int < 100:
            FOODON_ID = 'FOODON:000000{}'.format(id_int)
        elif id_int < 1000:
            FOODON_ID = 'FOODON:00000{}'.format(id_int)
        elif id_int < 10000:
            FOODON_ID = 'FOODON:0000{}'.format(id_int)
        elif id_int < 100000:
            FOODON_ID = 'FOODON:000{}'.format(id_int)
        elif id_int < 1000000:
            FOODON_ID = 'FOODON:00{}'.format(id_int)
        elif id_int < 10000000:
            FOODON_ID = 'FOODON:0{}'.format(id_int)
        elif id_int < 100000000:
            FOODON_ID = 'FOODON:{}'.format(id_int)
        id_int += 1
        created = ''
        contributor = ''
        see_also = ''
        exact_syn = ''
        broad_syn = ''
        narrow_syn = ''
        database_cross_reference = ''
        comment = ''
        editor_note = ''
        imported_from = ''
        definition_source = ''
        image = ''
        image_rights_holder = ''
        image_license = ''

    OutTup = namedtuple("OutTup", ["FOODON_ID", "new_id", "id_int", "created", "contributor", "see_also", "exact_syn", "broad_syn", "narrow_syn", "database_cross_reference", "comment", "editor_note", "imported_from", "definition_source", "image", "image_rights_holder", "image_license"])
    return OutTup(
        FOODON_ID, new_id, id_int, created, contributor, see_also, exact_syn, broad_syn, narrow_syn, database_cross_reference, comment, editor_note, imported_from, definition_source, image, image_rights_holder, image_license
    )


# --------------------------------------------------
def main():
    """Create robot template from list of input species and generic template"""
    args = get_args()
    template_arg = args.template
    template_list = []
    species_list = []
    input_mapping_list = []

    # open and save template file as list of OrderedDict
    with open(template_arg, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            template_list.append(row)

    # open and save input species file as list of OrderedDict
    with open(args.input, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            species_list.append(row)

    # open and save ID mapping file as list of OrderedDict
    with open(args.mapping, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            input_mapping_list.append(row)

    # Temp make list for dicts of Sheet_ID and Sheet_Label for output mapping file
    mappings_list = []

    # write out robot template tsv file
    with open(args.output, 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.writer(csv_file, delimiter='\t')
        #write header rows
        header_1 = ['ontology ID', 'parent class', 'label', 'Equivalence axiom', 'Subclass axiom', 'definition', 'comment', 'Exact Synonym', 'Broad Synonym', 'created', 'contributor', 'see also', 'curation_status', 'Alternative Label', 'Legacy Exact Synonym', 'Legacy Broad Synonym', 'Legacy Narrow Synonym', 'Database Cross Reference', 'Legacy Comment', 'Editors Note', 'Imported From', 'Definition Source', 'image', 'Image rights Holder', 'Image License']
        header_2 = ['ID', 'SC % SPLIT=|', 'AL rdfs:label@en', 'EC %', 'SC % SPLIT=|', 'AL IAO:0000115@en', 'AL rdfs:comment@en', 'AL oboInOwl:hasExactSynonym@en SPLIT=|', 'AL oboInOwl:hasBroadSynonym@en', 'AT dc:date^^xsd:dateTime', 'A dc:contributor SPLIT=|', 'A rdfs:seeAlso SPLIT=|', 'AI IAO:0000114', 'AL oboInOwl:hasExactSynonym@en', 'AL oboInOwl:hasExactSynonym@en SPLIT=|', 'AL oboInOwl:hasBroadSynonym@en SPLIT=|', 'AL oboInOwl:hasNarrowSynonym@en SPLIT=|', 'AI oboInOwl:hasDbXref SPLIT=|', 'AL rdfs:comment@en', 'AL IAO:0000116@en', 'AI IAO:0000412', 'AI IAO:0000119 SPLIT=|', 'AI schema:image', '>A dc:license', '>A dc:rightsHolder']
        writer.writerow(header_1)
        writer.writerow(header_2)

        for s in species_list:
            template = s['template']

            for index, item in enumerate(template_list):
                keep = ''
                if template == 'top':
                    keep = args.organism
                else:
                    keep = item[str(template)]

                if keep != '' and s['Compiled'] == 'TRUE':
                    filter_species = keep.split("|")
                    filter_species = [i for i in filter_species if i]
                    filter_species = [i.strip() for i in filter_species]
                    if s['species'] in filter_species or s['Alternative_name'] in filter_species or 'ALL' in filter_species:
                        if item['DISABLED'] != 'TRUE':

                            species_label = s['species']
                            alt_label = ''
                            if s['Alternative_name'] in filter_species:
                                species_label = s['Alternative_name']
                                alt_label = item['label'].format(organism=s['species'], organism_base=s['species'])

                            if item['parent class'] is None:
                                parent_class = ''
                            else:
                                parent_class = item['parent class'].format(organism=species_label, organism_base=s['species'])

                            label = item['label'].format(organism=species_label, organism_base=s['species'])

                            # Remove subclass axioms pointing to self
                            if label == parent_class:
                                parent_class = ''

                            # Generate sheet ID for new mapping table rows
                            Sheet_ID = item['ID'] + '-' + s['species']

                            # Continue ID range count from previous run
                            with open(args.identifier) as file:
                                id_int = int(file.readline())
                            file.close()

                            # Call helper function to pull data from input_mapping_list as well as assign new IDs
                            ID_res = assign_ID(input_mapping_list=input_mapping_list, Sheet_ID=Sheet_ID, id_int=id_int)
                            ID = ID_res.FOODON_ID

                            # Equivalence axiom
                            if s['species'] == args.organism:
                                if item['Equivalence Override'] == 'TRUE':
                                    equivalence = ''
                                elif item['Equivalence Override'] != '':
                                    equivalence = item['Equivalence Override'].format(organism=s['species'], taxon=s['ID'])
                                elif item['Equivalence axiom'] is None:
                                    equivalence = ''
                                else:
                                    equivalence = item['Equivalence axiom'].format(organism=s['species'], organism_base=s['species'], taxon=s['ID'])
                            else:
                                if item['Equivalence axiom'] is None:
                                    equivalence = ''
                                else:
                                    equivalence = item['Equivalence axiom'].format(organism=species_label, organism_base=s['species'], taxon=s['ID'])

                            if item['Subclass axiom'] is None:
                                subclass_axiom = ''
                            else:
                                subclass_axiom = item['Subclass axiom'].format(organism=species_label, organism_base=s['species'])

                            if item['definition'] is None:
                                definition = ''
                            else:
                                if s['species'][0].lower() in ['a', 'e', 'i', 'o', 'u']:
                                    article = 'n'
                                else:
                                    article = ''
                                definition = item['definition'].format(organism=species_label, organism_base=s['species'], n=article).capitalize()

                            if item['comment'] is None:
                                comment = ''
                            else:
                                comment = item['comment'].format(organism=species_label, organism_base=s['species'])

                            if item['Exact Synonym'] is None:
                                exact_synonym = ''
                            else:
                                exact_synonym = item['Exact Synonym'].format(organism=species_label)

                            if item['Broad Synonym'] is None:
                                broad_synonym = ''
                            else:
                                broad_synonym = item['Broad Synonym'].format(organism=species_label)

                            if ID_res.created != '':
                                created = ID_res.created
                            else:
                                created = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

                            if ID_res.contributor != '':
                                contributor = ID_res.contributor
                            else:
                                contributor = args.contributor

                            if ID_res.see_also != '':
                                see_also = ID_res.see_also
                            else:
                                see_also = ''

                            curation_status = 'ready for release'

                            if ID_res.exact_syn != '':
                                legacy_exact_syn = ID_res.exact_syn
                            else:
                                legacy_exact_syn = ''

                            if ID_res.broad_syn != '':
                                legacy_broad_syn = ID_res.broad_syn
                            else:
                                legacy_broad_syn = ''

                            if ID_res.narrow_syn != '':
                                legacy_narrow_syn = ID_res.narrow_syn
                            else:
                                legacy_narrow_syn = ''

                            if ID_res.database_cross_reference != '':
                                database_cross_reference = ID_res.database_cross_reference
                            else:
                                database_cross_reference = ''

                            if ID_res.comment != '':
                                legacy_comment = ID_res.comment
                            else:
                                legacy_comment = ''

                            if ID_res.editor_note != '':
                                editor_note = ID_res.editor_note
                            else:
                                editor_note = ''

                            if ID_res.imported_from != '':
                                imported_from = ID_res.imported_from
                            else:
                                imported_from = ''

                            if ID_res.definition_source != '':
                                definition_source = ID_res.definition_source
                            else:
                                definition_source = ''

                            if ID_res.image != '':
                                image = ID_res.image
                            else:
                                image = ''

                            if ID_res.image_rights_holder != '':
                                image_rights_holder = ID_res.image_rights_holder
                            else:
                                image_rights_holder = ''

                            if ID_res.image_license != '':
                                image_license = ID_res.image_license
                            else:
                                image_license = ''

                            # If a new ID was generated add it to the output mapping dict list
                            if ID_res.new_id is True:
                                mapping_dict = {'Sheet_ID': Sheet_ID,
                                                'Sheet_Label': label,
                                                'FOODON_ID': ID_res.FOODON_ID,
                                                'FOODON_Label': label,
                                                'Mapping_Type': 'Exact String Match',
                                                'Curation Notes': '',
                                                'created': created,
                                                'contributor': args.contributor,
                                                'see_also': see_also,
                                                'exact_syn': legacy_exact_syn,
                                                'broad_syn': legacy_broad_syn,
                                                'narrow_syn': legacy_narrow_syn,
                                                'database_cross_reference': database_cross_reference,
                                                'comment': legacy_comment,
                                                'editor_note': editor_note,
                                                'imported_from': imported_from,
                                                'definition_source': definition_source,
                                                'image': image,
                                                'image_rights_holder': image_rights_holder,
                                                'image_license': image_license
                                                }
                                mappings_list.append(mapping_dict)
                                # Write generated identifier to txt file
                                with open(args.identifier, 'w') as file:
                                    file.write(str(ID_res.id_int))
                                file.close()

                            row = [ID, parent_class, label, equivalence, subclass_axiom, definition, comment, exact_synonym, broad_synonym, created, contributor, see_also, curation_status, alt_label, legacy_exact_syn, legacy_broad_syn, legacy_narrow_syn, database_cross_reference, legacy_comment, editor_note, imported_from, definition_source, image, image_rights_holder, image_license]
                            writer.writerow(row)

    if not mappings_list:
        print('')
        print('No new terms to be added.')
        print('')
    else:
        # Write out Mapping list tsv file
        with open(args.new_terms, 'w', encoding='utf8', newline='') as output_file:
            fc = csv.DictWriter(output_file, fieldnames=mappings_list[0].keys(), delimiter='\t')
            fc.writerows(mappings_list)

# --------------------------------------------------
if __name__ == '__main__':
    main()
