#!/usr/bin/env python

# ===============================================================================
#    Copyright 2017 Geoscience Australia
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
# ===============================================================================
'''
CSV2NetCDFConverter concrete class for converting data to netCDF

Created on 28Mar.2018

@author: Andrew Turner
'''
#TODO update creationg date

from collections import OrderedDict
import numpy as np
import cx_Oracle
from geophys_utils.netcdf_converter import ToNetCDFConverter, NetCDFVariable
from geophys_utils import points2convex_hull
import sys
import re
from datetime import datetime
import yaml
import os
import logging


# # Create the Logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# Create the console handler and set logging level
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
# Create a formatter for log messages
logger_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Add the Formatter to the Handler
console_handler.setFormatter(logger_formatter)
# Add the Handler to the Logger
logger.addHandler(console_handler)


class Grav2NetCDFConverter(ToNetCDFConverter):
    '''
    CSV2NetCDFConverter concrete class for converting CSV data to netCDF
    '''

    gravity_metadata_list = [
        # 'ENO', not needed
        'SURVEYID',
        'SURVEYNAME',
        'COUNTRYID',
        'STATEGROUP',
        'STATIONS', #number of stations?
        # 'GRAVACC', - variable
        #'GRAVDATUM' as variable attribute
        # 'GNDELEVACC', - variable
        # 'GNDELEVMETH', - variable
        # 'GNDELEVDATUM', - variable - 6 outliers
        # 'RELIAB', variable - 5 outliers
        'LAYOUT',
        # 'ACCESS_CODE', filtered
        # 'ENTRYDATE', not needed
        # 'ENTEREDBY', not needed
        # 'LASTUPDATE', not needed
        # 'UPDATEDBY', not needed
        # 'GRAVACCUNITS', #always um. In grav acc var attribute - may be null sometimes
        # 'GRAVACCMETHOD', variable
        # 'GNDELEVACCUNITS',  # always m maybe some as null. In gravlevacc var attribut
        # 'GNDELEVACCMETHOD', as variable
        # 'ELLIPSOIDHGTDATUM',  # always - always GRS80 now as variable attriubte of ellipsoidhgt
        # 'ELLIPSOIDHGTMETH', methods deemed not required for analysis
        # 'ELLIPSOIDHGTACC', # as variable
        # 'ELLIPSOIDHGTACCMETHOD',# methods deemed not required for analysis
        # 'ELLIPSOIDHGTACCUOM', # as variable attribute
        'SURVEYTYPE',
        # 'DATATYPES', not needed
        # 'UNO', not needed
        'OPERATOR',
        'CONTRACTOR',
        'PROCESSOR',
        'CLIENT',  # nulls
        'OWNER',  # nulls
        'LEGISLATION',  # nulls
        # 'STATE',
        'PROJ_LEADER',  # nulls
        'ON_OFF', #?
        #'STARTDATE', moved to global attributes for enhanced searching
        #'ENDDATE', moved to global attributes for enhanced searching
        'VESSEL_TYPE',  # nulls
        'VESSEL',  # nulls
        'SPACEMIN',  # can add uom which is metres
        'SPACEMAX',
        # 'LOCMETHOD', -not needed
        #'ACCURACY',  as point variable
        #'GEODETIC_DATUM',
        #'PROJECTION', # the data is given in the netcdf as gda94 unprojected. The values in the projection are Ellispoids
        # 'QA_CODE', not needed
        #'RELEASEDATE',  # not needed but open for discussion
        #'COMMENTS',  # not needed but open for discussion
        # 'DATA_ACTIVITY_CODE',
        # 'NLAT', already in global attributes
        # 'SLAT', already in global attributes
        # 'ELONG', already in global attributes
        # 'WLONG', already in global attributes
        # 'ANO', not needed
        # 'QABY', not needed
        # 'QADATE', not needed
        # 'CONFID_UNTIL', not needed
    ]

    try:
        logger.debug(os.path.splitext(__file__)[0] + '_settings.yml')
        settings = yaml.safe_load(open(os.path.splitext(__file__)[0] + '_settings.yml'))
        logger.debug('Settings' + str(settings))
    except:
        logger.debug("Yaml load fail")
        settings = {}

    def get_keys_and_values_table(self, table_name: str):
        """
        Retrieves all data from a specified table, converts into a dictionary, and returns as a string. Used for tables
        with the key and value information such as accuray or methodology.
        e.g. 'SUR': 'Positions determined by optical surveying methods or measured on surveyed points.'
        """
        sql_statement = 'select * from gravity.{}'.format(table_name)
        query_result = self.cursor.execute(sql_statement)
        keys_and_values_dict = OrderedDict()
        for s in query_result:
            # for every instance in the table, add the 1st and 2nd column as key, value in a python dict
            keys_and_values_dict[s[0]] = s[1]

        # returns as string. Python dict not accepted.
        return keys_and_values_dict

    def get_value_for_key(self, value_column: str, table_name: str, key_column: str,  key: str):
        """
        Retrieves all data from a specified table, converts into a dictionary, and returns as a string. Used for tables
        with the key and value information such as accuracy or methodology.
        e.g. 'SUR': 'Positions determined by optical surveying methods or measured on surveyed points.'
        """

        cleaned_key = str(key)
        list_of_characters_to_remove = ["\(", "\)", "\'", "\,"]
        for character in list_of_characters_to_remove:
            cleaned_key = re.sub(character, '', cleaned_key)

        sql_statement = "select {0} from gravity.{1} where {2} = '{3}'".format(value_column, table_name, key_column, cleaned_key)
        query_result = self.cursor.execute(sql_statement)
        key_to_return = str(next(query_result))

        for character in list_of_characters_to_remove:
            key_to_return = re.sub(character, '', key_to_return)

        return key_to_return

    def __init__(self, nc_out_path, survey_id, con, sql_strings_dict_from_yaml, netcdf_format='NETCDF4'):
        """
        Concrete constructor for subclass CSV2NetCDFConverter
        Needs to initialise object with everything that is required for the other Concrete methods
        N.B: Make sure this base class constructor is called from the subclass constructor
        """

        ToNetCDFConverter.__init__(self, nc_out_path, netcdf_format)

        self.cursor = con.cursor()
        self.survey_id = survey_id
        self.sql_strings_dict_from_yaml = sql_strings_dict_from_yaml

        self.survey_metadata = self.get_survey_metadata()

    def get_survey_metadata(self):
        """
        Retrieve all data from the gravsurveys and joined a.surveys tables for the current surveyid in the loop.
        Uses same filters as other sql queries.

        :return:
        """
        # TODO are the filters needed in the sql? It will pass this survey id if no observation data is used later on?

        formatted_sql = self.sql_strings_dict_from_yaml['get_survey_metadata'].format(self.survey_id)
        query_result = self.cursor.execute(formatted_sql)
        field_names = [field_desc[0] for field_desc in query_result.description]
        survey_row = next(query_result)

        return dict(zip(field_names, survey_row))

    def get_survey_wide_value_from_obs_table(self, field):
        """
        Helper function to retrieve a survey wide value from the observations table. The returning value is tested
        to be the only possible value (or null) within that survey.
        :param field: The target column in the observations table.
        :return: The first value of the specified field of the observations table.
        """
        formatted_sql = self.sql_strings_dict_from_yaml['get_data'].format('o1.'+field, "null", self.survey_id)
        formatted_sql = formatted_sql.replace('select', 'select distinct', 1) # Only retrieve distinct results
        formatted_sql = re.sub('order by .*$', '', formatted_sql) # Don't bother sorting
        query_result = self.cursor.execute(formatted_sql)
        value = None

        for result in query_result:
            logger.debug('value: {}, result: {}'.format(value, result))
            
            if value is None:
                value = result[0]
            
            assert value is None or result[0] == value or result[0] is None, 'Variant value found in survey-wide column {}'.format(field)
        return value

    def get_global_attributes(self):
        '''
        Concrete method to return dict of global attribute <key>:<value> pairs
        '''        
        metadata_dict = {'title': self.survey_metadata['SURVEYNAME'],
                         'survey_id': self.survey_id,
            'Conventions': "CF-1.6,ACDD-1.3",
            'keywords': 'points, gravity, ground digital data, geophysical survey, survey {0}, {1}, {2}, Earth sciences,'
                        ' geophysics, geoscientificInformation'.format(self.survey_id, self.survey_metadata['COUNTRYID'], self.survey_metadata['STATEGROUP']),
            'geospatial_lon_min': np.min(self.nc_output_dataset.variables['longitude']),
            'geospatial_lon_max': np.max(self.nc_output_dataset.variables['longitude']),
            'geospatial_lon_units': "degrees_east",
            'geospatial_long_resolution': "point",
            'geospatial_lat_min': np.min(self.nc_output_dataset.variables['latitude']),
            'geospatial_lat_max': np.max(self.nc_output_dataset.variables['latitude']),
            'geospatial_lat_units': "degrees_north",
            'geospatial_lat_resolution': "point",
            'history': "Pulled from point gravity database at Geoscience Australia",
            'summary': "This gravity survey, {0}, {1} located in {2} measures the slight variations in the earth's "
            "gravity based on the underlying structure or geology".format(self.survey_id,
                                                                      self.survey_metadata['SURVEYNAME'],
                                                                      self.survey_metadata['STATEGROUP']),
            'location_accuracy_min': np.min(self.nc_output_dataset.variables['locacc']),
            'location_accuracy_max': np.max(self.nc_output_dataset.variables['locacc']),
            'time_coverage_start': str(self.survey_metadata.get('STARTDATE')),
            'time_coverage_end': str(self.survey_metadata.get('ENDDATE')),
            'time_coverage_duration': str(self.survey_metadata.get('ENDDATE') - self.survey_metadata.get('STARTDATE'))
                if self.survey_metadata.get('STARTDATE') else "Unknown",
            'date_created': datetime.now().isoformat(),
            'institution': 'Geoscience Australia',
            'source': 'ground observation',
            #'references': '',## Published or web-based references that describe the data or methods used to produce it.
            'cdm_data_type': 'Point'
            }

        try:
            #Compute convex hull and add GML representation to metadata
            coordinates = np.array(list(zip(self.nc_output_dataset.variables['longitude'][:],
                                            self.nc_output_dataset.variables['latitude'][:]
                                            )
                                        )
                                   )
            if len(coordinates) >=3:
                convex_hull = points2convex_hull(coordinates)        
                metadata_dict['geospatial_bounds'] = 'POLYGON((' + ', '.join([' '.join(
                    ['%.4f' % ordinate for ordinate in coordinates]) for coordinates in convex_hull]) + '))'
            if len(coordinates) == 2: # Two points - make bounding box
                bounding_box = [[min(coordinates[:,0]), min(coordinates[:,1])],
                                [max(coordinates[:,0]), min(coordinates[:,1])],
                                [max(coordinates[:,0]), max(coordinates[:,1])],
                                [min(coordinates[:,0]), max(coordinates[:,1])],
                                [min(coordinates[:,0]), min(coordinates[:,1])]
                                ]
                metadata_dict['geospatial_bounds'] = 'POLYGON((' + ', '.join([' '.join(
                    ['%.4f' % ordinate for ordinate in coordinates]) for coordinates in bounding_box]) + '))'
            if len(coordinates) == 1: # Single point
                #TODO: Check whether this is allowable under ACDD
                metadata_dict['geospatial_bounds'] = 'POINT((' + ' '.join(
                    ['%.4f' % ordinate for ordinate in coordinates[0]]) + '))'
        except:
            logger.warning('Unable to write global attribute "geospatial_bounds"')
            
        return metadata_dict

    def get_dimensions(self):
        '''
        Concrete method to return OrderedDict of <dimension_name>:<dimension_size> pairs
        '''

        formatted_sql = self.sql_strings_dict_from_yaml['get_dimensions'].format(self.survey_id)
        self.cursor.execute(formatted_sql)
        point_count = int(next(self.cursor)[0])

        dimensions = OrderedDict()
        dimensions['point'] = point_count  # number of points per survey

        for field_value in Grav2NetCDFConverter.settings['field_names'].values():
            if field_value.get('lookup_table'):
                lookup_dict = self.get_keys_and_values_table(field_value['lookup_table'])
                new_dimension_name = field_value['short_name'].lower()
                dimensions[new_dimension_name] = len(lookup_dict)
                # print(dimensions[new_dimension_name])
            else:
                pass
        # print(dimensions['point'])
        return dimensions

    def variable_generator(self):
        '''
        Concrete generator to yield NetCDFVariable objects

        '''

        def get_data(field_name_dict):
            """

            :param field_name_dict:
            :return:
            """
            # call the sql query and assign results into a python list
            # the sql format will be slightly different for freeiar and bouguer. Instead of simply o1.[variable],
            # they will instead insert a function with arguements. Thus the o1 isn't needed and an empty string is
            # instead passed to the sql string.
            if field_name in ['Freeair', 'Bouguer']:
                formatted_sql = self.sql_strings_dict_from_yaml['get_data'].format(field_name_dict['database_field_name'],
                                                                               field_name_dict['fill_value'],
                                                                               self.survey_id)

            else:
                formatted_sql = self.sql_strings_dict_from_yaml['get_data'].format('o1.'+field_name_dict['database_field_name'],
                                                                               field_name_dict['fill_value'],
                                                                               self.survey_id)


            try:
                self.cursor.execute(formatted_sql)
            except:
                logger.debug(formatted_sql)
                raise
            
            variable_list = []
            for i in self.cursor:
                variable_list.append(
                    i[0])  # getting the first index is required. Otherwise each point is within its own tuple.

            return variable_list

        def generate_ga_metadata_dict():
            gravity_metadata = {}
            for key, value in iter(self.survey_metadata.items()):
                for metadata_attribute in Grav2NetCDFConverter.gravity_metadata_list:
                    if value is not None:
                        if key == metadata_attribute:
                            if type(value) == datetime:
                                gravity_metadata[key] = value.isoformat()
                            else:
                                gravity_metadata[key] = value

                                # if isinstance(metadata_attribute, list):
                                #     if key == metadata_attribute[0]:
                                #         # get_value_for_key(value_column: str, table_name: str, key_column: str,  key: str)
                                #         gravity_metadata[key] = str(self.get_value_for_key('DESCRIPTION', metadata_attribute[1], key, value))

            logger.debug("GA gravity metadata")
            logger.debug(gravity_metadata)

            return gravity_metadata

        # def handle_key_value_cases_2(field_value, key_values_tables_dict):
        #     value_array = get_data(field_value)
        #     ka, ea = np.unique(value_array, return_inverse=True)
        #     print("ka: " + str(ka))
        #     print("ea: " + str(ea))
        #     return ea, ka
        #
        # def convert_list_to_mapped_values(list_to_edit, mapping_dict):
        #
        #     logger.debug('- - - - - - - - - - - - - - - - - -')
        #     logger.debug('convert_list_to_mapped_values()')
        #     logger.debug('list_to_edit: ' + str(list_to_edit))
        #     logger.debug('mapping_dict: ' + str(mapping_dict))
        #     transformed_list = []
        #
        #     for l in list_to_edit:
        #         for key5, value5, in mapping_dict.items():
        #             if l == key5:
        #                 transformed_list.append(mapping_dict.get(key5))
        #             else:
        #                 pass
        #     return transformed_list

        def handle_key_value_cases(field_value, lookup_table_dict):
            """
            """
            logger.debug('- - - - - - - - - - - - - - - -')
            logger.debug('handle_key_value_cases() with field value: ' + str(field_value) + ' and key_value_tables_dict: ' + str(lookup_table_dict))

            # get the keys into a list
            lookup_key_list = [lookup_key for lookup_key in lookup_table_dict.keys()]

            # for key, value2 in lookup_table_dict.items():
            #     key_list.append(key)
            #     value_list.append(value2)

            # create the lookup table to convert variables with strings as keys.
            lookup_dict = {lookup_key: lookup_key_list.index(lookup_key)
                           for lookup_key in lookup_key_list}
            
            # get the array of numeric foreign key values
            field_data_array = get_data(field_value)

            # transform the data_list into the mapped value.
            #transformed_list = convert_list_to_mapped_values(value_array, mapping_dict) # using the function
            transformed_list = [lookup_dict.get(lookup_key) for lookup_key in field_data_array]
                                
            #===================================================================
            # transformed_list =[]                   
            # for field_data in field_data_array:
            #     [transformed_list.append(lookup_dict.get(lookup_key)) for lookup_key, lookup_value, in lookup_dict.items()
            #      if field_data == lookup_key]
            #===================================================================

            # loop through the table_key_dict and the lookup table. When a match is found add the new mapped key to
            # the existing value of the table_key_dict in a new dict
            converted_dict = {lookup_table_dict[key]: value 
                              for key, value in lookup_table_dict.items()}
            
            #===================================================================
            # converted_dict = {}
            # for keys, values in lookup_table_dict.items():
            #     for map_key, map_value in lookup_dict.items():
            #         if keys == map_key:
            #             converted_dict[map_value] = lookup_table_dict[keys]
            #===================================================================

            return transformed_list, converted_dict

        def get_field_description(target_field):
            """
            Helper function to retrieve the field description from a connected oracle database
            :param target_field:
            :return field_description:
            """
            sql_statement = self.sql_strings_dict_from_yaml['get_field_description'].format(target_field.upper())
            self.cursor.execute(sql_statement)
            field_description = str(next(self.cursor)[0])

            return field_description


        def wrangle_data_and_attributes_to_be_netcdfified(field_name, field_value):
            """

            """
            # values to parse into NetCDFVariable attributes list. Once passed they become a netcdf variable attribute.
            # lookup_table is later converted to comments.
            list_of_possible_value = ['long_name', 'standard_name', 'units', 'dtype', 'lookup_table', 'dem', 'datum']

            logger.debug('-----------------')
            logger.debug("Field Name: " + str(field_name))
            logger.debug("Field Values: " + str(field_value))
            converted_data_array = []
            attributes_dict = {}

            for value in list_of_possible_value:
                logger.debug("Value in list_of_possible_value: " + str(value))
                # if the field value is in the list of accepted values then add to attributes dict
                if field_value.get(value):
                    logger.debug("Processing: " + str(value))

                    # some key values are already int8 and don't need to be converted. Thus a flag is included in the
                    # field_names
                    if value == 'lookup_table':
                        logger.debug('Converting ' + str(value) + 'string keys to int8 with 0 as 1st index')
                        converted_data_list, converted_key_value_dict = handle_key_value_cases(field_value,
                                                    self.get_keys_and_values_table(field_value.get('lookup_table')))

                        logger.debug('Adding converted lookup table as variable attribute...')
                        # this replaces ['comments'] values set in the previous if statement.
                        # attributes_dict['comments'] = str(converted_key_value_dict)
                        converted_data_array = np.array(converted_data_list, field_value['dtype'])

                    # for the one case where a column in the observation table (tcdem) needs to be added as the
                    # attribute of varaible in the netcdf file.
                    if value == 'dem' or value == 'datum':
                        # the grav datum needs to be converted from its key value
                        if field_value.get('short_name') == 'Grav':
                            gravdatum_key = self.get_survey_wide_value_from_obs_table(field_value.get(value))
                            attributes_dict[value] = self.get_value_for_key("DESCRIPTION", "GRAVDATUMS", "GRAVDATUM", gravdatum_key)
                        # while TCDEM and ELLIPSOIDHGTDATUM do not
                        else:
                            attributes_dict[value] = self.get_survey_wide_value_from_obs_table(field_value.get(value))
                            # if None is returned then remove the attribute
                            if attributes_dict[value] is None:
                                attributes_dict.pop(value)
                            else:
                                pass

                    # for all other values, simply add them to attributes_dict
                    else:
                        attributes_dict[value] = field_value[value]
                        logger.debug('attributes_dict["{}"] = {}'.format(value, field_value[value]))
                # if the value isn't in the list of accepted attributes
                else:
                    logger.debug(str(value) + ' is not found in yaml config or is not set as an accepted attribute.')

            logger.debug('Attributes_dict' + str(attributes_dict))

            # if the data array contained a lookup and was converted, return it and the attribute dict.
            if len(converted_data_array) > 0:
                return converted_data_array, attributes_dict

            # else get the non converted data and return it in an numpy array and the and the attribute dict too
            else:
                data_array = np.array(get_data(field_value), dtype=field_value['dtype'])
                return data_array, attributes_dict

        # ------------------------------------------------------------------------------------
        # Begin yielding NetCDFVariables
        # ------------------------------------------------------------------------------------
        # ---------------------------------------------------------------------------
        # crs variable creation for GDA94
        # ---------------------------------------------------------------------------
        yield self.build_crs_variable('''\
        GEOGCS["GDA94",
            DATUM["Geocentric_Datum_of_Australia_1994",
                SPHEROID["GRS 1980",6378137,298.257222101,
                    AUTHORITY["EPSG","7019"]],
                TOWGS84[0,0,0,0,0,0,0],
                AUTHORITY["EPSG","6283"]],
            PRIMEM["Greenwich",0,
                AUTHORITY["EPSG","8901"]],
            UNIT["degree",0.0174532925199433,
                AUTHORITY["EPSG","9122"]],
            AUTHORITY["EPSG","4283"]]
        '''
                                          )
        # ---------------------------------------------------------------------------
        # non acc convention survey level metadata grouped into one variable
        # ---------------------------------------------------------------------------


        yield NetCDFVariable(short_name='ga_gravity_metadata',
                             data=0,
                             dimensions=[],  # Scalar
                             fill_value=None,
                             attributes=generate_ga_metadata_dict(),
                             dtype='int8'  # Byte datatype
                             )


        # ---------------------------------------------------------------------------
        # The point dimension variables and their assocciated lookup table variables
        # ---------------------------------------------------------------------------
        # Loop through the defined variables in the yaml config and construct as netcdf variables.
        for field_name, field_value in Grav2NetCDFConverter.settings['field_names'].items():
            # convert strings to int or floats for int8 and float32 to get the required data type for the fill value
            if field_value['dtype'] == 'int8':
                fill_value = int(field_value['fill_value'])
            elif field_value['dtype'] == 'float32':
                fill_value = float(field_value['fill_value'])
            else:
                fill_value = field_value['fill_value']

            data, attributes = wrangle_data_and_attributes_to_be_netcdfified(field_name, field_value)

            if field_value.get('lookup_table'):

                # get the values from the lookup table dict and convert into a np.array
                lookup_table_dict = self.get_keys_and_values_table(field_value['lookup_table'])
                grid_value_list = [value for value in iter(lookup_table_dict.values())]
                lookup_table_array = np.array(grid_value_list)
                attributes.pop('dtype', None)
                attributes.pop('lookup_table', None)

                dim_name = field_value['short_name'].lower()

                # Yield lookup table with same name as field
                yield NetCDFVariable(short_name=dim_name,
                                     data=lookup_table_array,
                                     dimensions=[dim_name],
                                     fill_value=fill_value,
                                     attributes=attributes
                                     )
                
                # Yield index table with name of <field_name>_index 
                index_attributes = dict(attributes)
                index_attributes['long_name'] = "zero-based index of value in " + dim_name
                index_attributes['lookup'] = dim_name
                    
                yield NetCDFVariable(short_name=((field_value.get('standard_name') or field_value['short_name']) + '_index').lower(),
                                     data=data,
                                     dimensions=['point'],
                                     fill_value=fill_value,
                                     attributes=index_attributes
                                     )
                
            else: # Not a lookup field
                yield NetCDFVariable(short_name=(field_value.get('standard_name') or field_value['short_name']).lower(),
                                     data=data,
                                     dimensions=['point'],
                                     fill_value=fill_value,
                                     attributes=attributes
                                     )

def main():

    # get user input and connect to oracle
    assert len(sys.argv) >= 4, '....'
    nc_out_path = sys.argv[1]
    u_id = sys.argv[2]
    oracle_database = sys.argv[3]
    pw = sys.argv[4]
    con = cx_Oracle.connect(u_id, pw, oracle_database)
    survey_cursor = con.cursor()

    # get sql strings from yaml file
    yaml_sql_settings = yaml.safe_load(open(os.path.splitext(__file__)[0] + '_sql_strings.yml'))
    sql_strings_dict = yaml_sql_settings['sql_strings_dict']
    # execute sql to return surveys to convert to netcdf

    survey_cursor.execute(sql_strings_dict['sql_get_surveyids'])
    
    # tidy the survey id strings
    survey_id_list = [re.search('\d+', survey_row[0]).group()
                      for survey_row in survey_cursor
                      ]

    logger.debug('Survey count = {}'.format(len(survey_id_list)))
    # Loop through he survey lists to make a netcdf file based off each one.
    for survey in survey_id_list:
        logger.debug("Processing for survey: " + str(survey))
        #try:
        g2n = Grav2NetCDFConverter(nc_out_path + "/" + str(survey) + '.nc', survey, con, sql_strings_dict)

        g2n.convert2netcdf()
        logger.info('Finished writing netCDF file {}'.format(nc_out_path))
        logger.info('-------------------------------------------------------------------')
        logger.info('Global attributes:')
        logger.info('-------------------------------------------------------------------')
        for key, value in iter(g2n.nc_output_dataset.__dict__.items()):
            logger.info(str(key) + ": " + str(value))
        logger.info('-'*30)
        logger.info('Dimensions:')
        logger.info('-'*30)
        logger.info(g2n.nc_output_dataset.dimensions)
        logger.info('-'*30)
        logger.info('Variables:')
        logger.info('-'*30)
        logger.info(g2n.nc_output_dataset.variables)

        #print(g2n.nc_output_dataset.file_format)
        #print(g2n.nc_output_dataset.variables[''])
        #print(g2n.nc_output_dataset.variables)
        # for data in g2n.nc_output_dataset.variables['Reliab lookup table']:
        #     print(data)
        del g2n
        # except Exception as e:




if __name__ == '__main__':
    main()
