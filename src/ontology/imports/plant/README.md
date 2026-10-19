# Plant Template: how to run it

Covers the plant side of FoodOn (apple, onion, walnut, etc).

This page covers the day-to-day steps. For the full picture, including what every file means, how the picklists work, and troubleshooting, see the [Plant Template](https://github.com/FoodOntology/foodon/wiki/Plant-Template) wiki page.

## Run commands from here

`src/ontology/scripts/`

## Step 1: test run

Safe to run as many times as you want, since nothing real gets changed.

```
sh run_organism_parts.sh MODE=test MODULE=plant TERM_CONTRIBUTOR_STR=<your orcid>
```

Look at `imports/plant/TEST_RUN_RESULTS/mapping_table_output.tsv`. It lists every new term this would create. If something looks wrong, stop here and fix it first. Column 3 (`FOODON_ID`) is the real new ID for the term. Column 1 (`Sheet_ID`) is only used internally by the script and can be ignored.

## Step 2: real run

```
sh run_organism_parts.sh MODE=run MODULE=plant TERM_CONTRIBUTOR_STR=<your orcid>
```

Answer "yes" when it asks if you already tested. This one hands out real, permanent IDs that can't be changed or reused afterward.

## Step 3: commit

The script tells you which files to commit. It's always some combination of
`plant_parts.tsv`, `plant_parts.ofn`, `id_integer.txt`, and `id_mapping_table.tsv`.

It also adds `menus.tsv` whenever that changed, and `plants.tsv` whenever that changed, since both get fetched fresh from the live sheet every run.

One more worth committing every time, even though the script lists it too: `plant_compile_history.tsv`. It adds one new row per real run (date, species compiled, total terms, how many were brand-new), so it builds up a running history of how much the Plant Template has produced over time.

The same goes for `species_added_log.tsv`. The first time any species is compiled, it gets one row recording the date and which contributor added it. Later real runs never touch that row again, even if the species gets recompiled, so this file is the permanent record of who added each species and when.

## Two messages you might see

```
[plant] 3 plant term(s) could NOT be generated for your selected species.
```

This means something you wanted is missing. Check `plant_compile_skip_log.txt` and fix it before doing a real run.

```
[plant] 42 item(s) flagged for review. These are not errors; everything compiled fine. See plant_curation_flags.txt.
```

Unlike the first message, this one does not indicate a problem. It's simply a list for the curator to review when time allows. For example, it used to flag "citrus fruit" because its scientific name (`Citrus`) is only identified to genus level, not an exact species. That's not wrong, just worth a second look. Once that got marked as reviewed, by setting its `logic` column, it stopped showing up. It's not urgent and doesn't block anything. If nothing's flagged, ignore it. Both files are fine to delete anytime, and the full explanation of what each one means is on the wiki. Both checks only ever look at species where `Compiled` is `TRUE`. A row you're still working on and haven't turned on yet will never show up here.

### Clearing a genus-level flag

Every species has a two-part scientific name: a genus, which is the broader group (like `Citrus`), and the exact species within it (like `Citrus japonica`). A row gets this flag when its `taxon` cell only has the genus part, not the exact species, and `logic` is still blank. A blank `logic` cell means the default equivalence axiom applies, which claims this row IS the entire genus. That's right for some rows and wrong for others, and the check can't tell which until someone decides.

So when a row is flagged, open it in `plants.tsv` and decide which of these is actually true, then type the matching word into that row's `logic` cell.

`only equivalency` means this row is meant to equal the whole genus. Use it for a generic food term where every species under that genus should count as this term too. For example, "citrus fruit" should equal `Citrus`, so any citrus species added later automatically counts as a citrus fruit.

`only subclassOf` means this row is one specific item that doesn't have its own exact species match yet, and should only be a kind of the genus, not equal to it. For example, "kumquat" shares the genus `Citrus` with "citrus fruit." If both were set to equivalence, kumquat and citrus fruit would become the same thing, which is wrong, since kumquat is only one kind of citrus fruit.

Either word clears the flag for good. It won't come back on future runs, because the check now reads that cell as a recorded decision instead of a blank. That also keeps the flag list honest, since it only ever shows rows nobody's decided on yet, so a genuinely new flag is easy to spot instead of getting lost among repeats.

Right below those two messages, every run also prints a line like:

```
[plant] <species compiled> species compiled, <terms generated> term(s) generated in total.
```

It's just a count of how many species were included and how many total terms came out of that run, not a pass/fail signal, just a sanity-check number.

## Checking for duplicate terms

After a test run, you can also run `find_duplicate_terms.py` to check whether any of the new terms it would create already exist in FoodOn under a different ID. It's a separate, optional step, not something the test run does on its own.

The script is in the same folder as the other commands, `src/ontology/scripts/`, and needs to be run from there, the same as the commands in Step 1 and Step 2.

```
python3 find_duplicate_terms.py plant
```

It only looks for an exact label match, or the same words written in a different order, so it will miss some real duplicates. That's intentional. It's meant to catch clear cases, not guess at loose similarities.

If it finds anything, it writes a file called `possible_duplicate_matches.tsv` in the same `TEST_RUN_RESULTS` folder as the test run's own output, listing each new term next to the existing term and ID it matches. If nothing matches, it says so and doesn't write a file.

Check each row by hand. If a match really is the same thing, use `--merge-id-reuse` to map it to the existing ID instead of creating a new one, the same way described in the next section.

Run it any time after Step 1, before deciding to do the real run in Step 2. It's safe to run more than once, since it never changes anything. It only reads and reports.

## Reusing an existing ID instead of making a new one

Sometimes the term a new species row would generate already exists in FoodOn under a different ID, for example because two different parts actually refer to the same real-world thing. "Onion" and "onion bulb" are a case like this: the bulb is really just what people mean by "onion" in everyday use, so instead of creating a second, separate ID for one of them, it gets mapped to reuse the ID that already exists for the other.

Unlike `plants.tsv`, `menus.tsv`, and `plant_parts.tsv`, the mapping file `id_mapping_table.tsv` is not fetched from a live Google Sheet. It's a plain file kept in the repo, and it only grows when this script adds rows to it directly. If a separate Google Sheet copy is kept for reference, it won't update on its own; any new rows need to be copied there by hand.

Finding which new terms need this takes checking each one against the ontology by hand, one at a time. `find_duplicate_terms.py` (see "Checking for duplicate terms" above) does that automatically for a whole test run at once, and lists any term it thinks already exists elsewhere. That's usually a faster way to find candidates for the mapping below than checking manually.

To add a reuse mapping:

1. Make a simple tab-separated file with these columns: `species`, `menu_name`, `title`, `existing_FOODON_ID`, `notes`.
2. Run the command below with `--dry-run` first. This previews what it would add, without changing anything yet, so it can be checked before going further. Include `-c` with your own ORCID, the same way you would for a normal run, so the rows it adds record who added them.

```
python3 organism_parts.py --merge-id-reuse your_file.tsv --dry-run \
  -t ../imports/plant/plant_parts.tsv \
  -i ../imports/plant/resources/plants.tsv \
  -mu ../imports/plant/resources/menus.tsv \
  -m ../imports/plant/resources/id_mapping_table.tsv \
  -c <your orcid>
```

3. If the preview looks right, run the same command again without `--dry-run` to actually add the rows. It only ever adds rows, never changes anything already there, so it's safe to run more than once.

## One rule

Never run `organism_parts.py` on its own against the real `id_integer.txt`. Always go through `run_organism_parts.sh`. That file is a one-way counter.
