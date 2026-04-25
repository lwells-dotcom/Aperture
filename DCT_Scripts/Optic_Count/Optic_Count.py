import Define_Optic_Count
file_type_for_cutsheet = "cutsheet"
file_type_for_ib = "infini_band"
file_type_for_roce = "roce"

files_to_count = Define_Optic_Count.menu()

for file in files_to_count:
    final_count_cutsheet = []
    final_count_ib = []
    final_count_roce = []
    file_type = Define_Optic_Count.get_file_type(file)
    if file_type == file_type_for_cutsheet:
        current_count = Define_Optic_Count.count_cutsheet(file)
        final_count_cutsheet.extend(current_count)
    if file_type == file_type_for_ib:
        current_count = Define_Optic_Count.count_infini_band(file)
        final_count_ib.extend(current_count)
    if file_type == file_type_for_roce:
        current_count = Define_Optic_Count.count_roce(file)
        final_count_roce.extend(current_count)

    print(f"Total Count for file: {file}")
    for optic_type in final_count_cutsheet:
        optic_type.print_count()
    for optic_type in final_count_ib:
        optic_type.print_count()
    for optic_type in final_count_roce:
        optic_type.print_count()



