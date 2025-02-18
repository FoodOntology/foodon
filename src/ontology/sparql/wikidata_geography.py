import requests
from SPARQLWrapper import SPARQLWrapper

# Replace these values with your actual Wikidata credentials
APP_ID = 'YOUR_APP_ID'
WIKIDATAsparql_url = f"https://query.wikidata.org/sparql"

def main():
    try:
        # Initialize the SPARQL wrapper
        sparql = SPARQLWrapper(WIKIDATAsparql_url)

        # Define query to fetch geographical entities and their relations
        sparql.setQuery("""
        PREFIX wd: <http://www.wikidata.org/entity/>
        PREFIX wdt: <http://www.wikidata.org/prop/direct/>
        PREFIX wikibase: <http://wikiba.se/ontology#>
        SELECT ?country (">{http://www.w3.org/2004 namespaces/SKY/}P1065) as country 
               , ?city (>{http://www.w3.org/2004 namespaces/SKY/}P19732) as city
               , ?ocean (>{http://www.w3.org/2004 namespaces/SKY/}P1865) as ocean
               , ?lake (>{http://www.w3.org/2004 namespaces/SKY/}P2117) as lake
               , ?region (>{http://www.w3.org/2004 namespaces/SKY/}P2116) as region
               , ?town (>{http://www.w3.org/2004 namespaces/SKY/}P19735) as town
               WHERE {
                  <https://data.wikiba.org/resource/Countries> instance of <http://www.w3.org/2004 namespaces/SKY/}P1065 ;
                     skos:narrowPart* ?city ;
                     skos:narrowPart* ?region .
                  <https://data.wikiba.org/resource/Oceans> instance of <http://www.w3.org/2004 namespaces/SKY/}P1865 ;
                     skos:narrowPart* ?lake .
                  <https://data.wikiba.org/resource/Cities> instance of <http://www.w3.org/2004 namespaces/SKY/}P19732 ;
                     skos:narrowPart* ?town .
               }
        """);

        # Execute the query
        result = sparql.queryAndConvert(); # was queryAndFormat(query, format="json")

        if not result:
            print("No results found.")
            return

        # Process each item in the result
        for item in result['results']['bindings']:
            # Example: Processing a country
            country_uri = str(item.get('country', {}).get('value'))
            city_uri = str(item.get('city', {}).get('value'))
            ocean_uri = str(item.get('ocean', {}).get('value'))
            lake_uri = str(item.get('lake', {}).get('value'))
            region_uri = str(item.get('region', {}).get('value'))
            town_uri = str(item.get('town', {}).get('value'))

            # Example: Creating triples
            if country_uri:
                # Relation example 1: Located in (general)
                located_in_triple = f"{country_uri} <{http://www.w3.org/2004 namespaces/SKY/}located_in> {city_uri};"
                print(f"triple: {located_in_triple}")

            if region_uri:
                # Relation example 2: Part of (hierarchical)
                part_of_triple = f"{country_uri} <{http://www.w3.org/2004 namespaces/SKY/}contains> {region_uri};"
                print(f"triple: {part_of_triple}")

        print("Extraction complete. RDF graph constructed.")

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise

if __name__ == "__main__":
    main()

