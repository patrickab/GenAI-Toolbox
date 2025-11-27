#!/bin/bash

# Executes [MinerU](https://github.com/opendatalab/MinerU) - GPU required

# Iterate over all PDF files in the current directory
for file in src/static/*.pdf; do
  # Check if the file exists and is a regular file
  if [[ -f "$file" ]]; then
    # Extract the filename without the extension
    filename=$(basename -- "$file")
    extension="${filename##*.}"
    filename_no_ext="${filename%.*}"

    # Create new filename
    new_filename="converted_${filename_no_ext}.pdf"

    # Perform conversion
    echo "Converting '$file' to '$new_filename'"
    mineru -p "$file" -o "$new_filename"
  fi
done
