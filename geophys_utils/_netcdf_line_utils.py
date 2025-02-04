#!/usr/bin/env python

#===============================================================================
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
#===============================================================================
'''
Created on 16/11/2016

@author: Alex Ip
'''
import os
import sys
import numpy as np
from geophys_utils._netcdf_point_utils import NetCDFPointUtils
from geophys_utils._crs_utils import transform_coords, get_spatial_ref_from_wkt
from geophys_utils._polygon_utils import points2convex_hull
from scipy.spatial.distance import pdist
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString
import logging
import netCDF4
from pprint import pformat
import shapely.wkt
from geophys_utils._transect_utils import utm_coords, coords2distance
from shapely.geometry import Polygon, MultiPolygon, asPolygon

# Setup logging handlers if required
logger = logging.getLogger(__name__) # Get logger
logger.setLevel(logging.INFO) # Initial logging level for this module
    
# Median point spacing parameters
COMPUTE_MEDIAN_SAMPLE_SPACING=True
POINT_STRIDE=10 # Number of points to skip when computing median sample spacing (1 = no skip)

DEFAULT_CHUNK_SPEC = {'point': 1024}

DEFAULT_VAR_OPTIONS = {'complevel': 5, 
                       'zlib': True, 
                       'fletcher32': True,
                       'shuffle': True,
                       'endian': 'little',
                       }
    
class NetCDFLineUtils(NetCDFPointUtils):
    '''
    NetCDFLineUtils class to do various fiddly things with NetCDF geophysics line data files.
    '''

    def __init__(self, 
                 netcdf_dataset,
                 memcached_connection=None,
                 enable_disk_cache=None,
                 enable_memory_cache=True,
                 cache_path=None,
                 debug=False):
        '''
        NetCDFLineUtils Constructor
        @parameter netcdf_dataset: netCDF4.Dataset object containing a line dataset
        @parameter enable_disk_cache: Boolean parameter indicating whether local cache file should be used, or None for default 
        @parameter enable_memory_cache: Boolean parameter indicating whether values should be cached in memory or not.
        @parameter debug: Boolean parameter indicating whether debug output should be turned on or not
        '''     
        # Start of init function - Call inherited constructor first
        super().__init__(netcdf_dataset=netcdf_dataset, 
                         memcached_connection=memcached_connection,
                         enable_disk_cache=enable_disk_cache, 
                         enable_memory_cache=enable_memory_cache,
                         cache_path=cache_path,
                         debug=debug)

        logger.debug('Running NetCDFLineUtils constructor')
        
        assert 'line' in self.netcdf_dataset.dimensions, 'No "line" dimension found'

        # Initialise private property variables to None until set by property getter methods
        self._line = None
        self._line_index = None
            
        
    def get_line_masks(self, line_numbers=None, subset_mask=None, get_contiguous_lines=False):
        '''
        Generator to return boolean masks of dimension 'point' for specified lines
        @param line_numbers: list of integer line number or single integer line number, or None for all lines
        @param subset_mask: optional Boolean mask for subset (e.g. spatial mask)
        @param get_contiguous_lines: Boolean flag indicating whether masked gaps in lines should be included
        
        @return line_number: line number for single line
        @return line_mask: Boolean mask for single line

        '''       
        # Yield masks for all lines in subset if no line numbers specified
        if line_numbers is None:
            line_number_subset = self.line # All line numbers
        else:
            # Convert single line number to single element list
            try:
                _line_numbers_iterator = iter(line_numbers)
            except TypeError:
                line_numbers = [line_numbers]

            line_number_subset = np.array(line_numbers)
            
        if subset_mask is not None:
            line_number_subset = line_number_subset[np.isin(line_number_subset, self.line[np.unique(self.line_index[subset_mask])])] # Exclude lines not in subset
        else:    
            line_number_subset = line_number_subset[np.isin(line_number_subset, self.line)] # Exclude bad line numbers 
    
        line_mask = np.zeros(shape=self.line_index.shape, dtype=np.bool) # Keep re-using same in-memory array

        for line_number in line_number_subset:
            line_mask[:] = False
            line_index = int(np.where(self.line == line_number)[0])
            
            if subset_mask is not None:
                line_mask[subset_mask] = (self.line_index[subset_mask] == line_index)
                
                if get_contiguous_lines:
                    # Include all points in line from first to last in subset
                    point_indices = np.where(line_mask)[0]
                    line_mask[min(point_indices):max(point_indices)+1] = True
            else:
                line_mask[(self.line_index == line_index)] = True
                
            #logger.debug('Line {} has a total of {} points'.format(line_number, np.count_nonzero(line_mask))) 
            
            if np.any(line_mask): # This is probably redundant
                yield line_number, line_mask
    
    
    def get_lines(self, line_numbers=None, 
                  variables=None, 
                  bounds=None, 
                  #bounds_wkt=None,
                  subsampling_distance=None,
                  get_contiguous_lines=False
                  ):
        '''
        Generator to return coordinates and specified variable values for specified lines
        @param line_numbers: list of integer line number or single integer line number
        @param variables: list of variable name strings or single variable name string. None returns all variables
        @param bounds: Spatial bounds for point selection
        @param bounds_wkt: WKT for bounds Coordinate Reference System 
        @param subsampling_distance: Minimum subsampling_distance expressed in native coordinate units (e.g. degrees)
        @param get_contiguous_lines: Boolean flag indicating whether masked gaps in lines should be included
        
        @return line_number: line number for single line
        @return: dict containing coords and values for required variables keyed by variable name
        '''
        # Return all variables if specified variable is None
        variables = self.point_variables if variables is None else variables
        
        # Allow single variable to be given as a string
        single_var = (type(variables) == str)
        if single_var:
            variables = [variables]
        
        bounds = bounds or self.bounds
        
        #spatial_subset_mask = self.get_spatial_mask(get_reprojected_bounds(bounds, bounds_wkt, self.wkt))
        spatial_subset_mask = self.get_spatial_mask(bounds)
        
        logger.debug('subsampling_distance: {}'.format(subsampling_distance))
        
        for line_number, line_mask in self.get_line_masks(line_numbers=line_numbers, 
                                                          subset_mask=spatial_subset_mask,
                                                          get_contiguous_lines=get_contiguous_lines
                                                          ):
        
            point_indices = np.where(line_mask)[0]
            #logger.debug('Line {} has {} points in bounding box'.format(line_number, len(point_indices))) 
            line_point_count = len(point_indices)
            if line_point_count: # This test should be redundant
                # Use subset of indices if stride is set
                if subsampling_distance:
                    line_length = pdist([self.xycoords[point_indices[0]], self.xycoords[point_indices[-1]]])[0]
                    logger.debug('line_length: {}'.format(line_length))
                    stride = max(1, int(line_point_count/max(1, line_length/subsampling_distance)))
                    logger.debug('stride: {}'.format(stride))
                    # Create array of subset indices, including the index of the last point if not already in subsample indices
                    subset_indices = np.unique(np.concatenate((np.arange(0, line_point_count, stride),
                                                              np.array([line_point_count-1])),
                                                              axis=None)
                                                              )
                    logger.debug('Subset of line {} has {} points'.format(line_number, len(subset_indices)))
                    point_indices = point_indices[subset_indices]
                    
                line_dict = {'coordinates': self.xycoords[point_indices]}
                # Add <variable_name>: <variable_array> for each specified variable
                for variable_name in variables:
                    line_dict[variable_name] = self.netcdf_dataset.variables[variable_name][point_indices]
        
                yield line_number, line_dict
 
    
    def get_line_values(self):
        '''
        Function to retrieve array of line number values from self.netcdf_dataset
        '''
        line_variable = self.netcdf_dataset.variables.get('line')
        assert line_variable, 'Variable "line" does not exist in netCDF file'
        if line_variable.shape: # Multiple lines
            #line_values = self.fetch_array(line_variable)
            line_values = line_variable[:] # Should be small enough to retrieve in one hit
            
        else: # Scalar - only one line
            line_values = line_variable[:].reshape((1,)) # Change scalar to single-element 1D array
            
        return line_values
    
    def get_line_index_values(self):
        '''
        Function to retrieve array of line_index indices from self.netcdf_dataset
        '''
        if len(self.netcdf_dataset.variables['line']): # Multiple lines
            line_index_variable = self.netcdf_dataset.variables.get('line_index')
            if line_index_variable: # Lookup format lines - Current format
                line_indices = self.fetch_array(line_index_variable)
                #line_indices = line_index_variable[:]
            else: # Indexing format lines - OLD FORMAT
                raise BaseException('Line data is in indexing format (unsupported)')
            
        else: # Scalar
            # Synthesize line_indices array with all zeroes for single value
            line_indices = np.zeros(shape=(self.point_count,), 
                                    dtype='int8'
                                    ) 
            
        return line_indices
      
    def get_cached_line_arrays(self):
        '''
        Helper function to cache both line & line_index
        '''
        line = None
        line_index = None

        if self.enable_disk_cache:
            if os.path.isfile(self.cache_path):
                # Cached coordinate file exists - read it
                cache_dataset = netCDF4.Dataset(self.cache_path, 'r')
                
                #assert cache_dataset.source == self.nc_path, 'Source mismatch: cache {} vs. dataset {}'.format(cache_dataset.source, self.nc_path)
                
                if 'line' in cache_dataset.variables.keys():
                    line = cache_dataset.variables['line'][:]
                    logger.debug('Read {} lines from cache file {}'.format(line.shape[0], self.cache_path))
                else:
                    logger.debug('Unable to read line variable from netCDF cache file {}'.format(self.cache_path))                

                if 'line_index' in cache_dataset.variables.keys():
                    line_index = cache_dataset.variables['line_index'][:]
                    logger.debug('Read {} line_indices from cache file {}'.format(line_index.shape[0], self.cache_path))
                else:
                    logger.debug('Unable to read line variable from netCDF cache file {}'.format(self.cache_path))  
                                  
                cache_dataset.close()
            else:
                logger.debug('NetCDF cache file {} does not exist'.format(self.cache_path))


            if line is None or line_index is None:
                if line is None:
                    line = self.get_line_values()
                if line_index is None:
                    line_index = self.get_line_index_values()
                
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                if os.path.isfile(self.cache_path):
                    cache_dataset = netCDF4.Dataset(self.cache_path, 'r+')
                else:
                    cache_dataset = netCDF4.Dataset(self.cache_path, 'w')
                
                if not hasattr(cache_dataset, 'source'):
                    cache_dataset.source = self.nc_path
                    
                #assert cache_dataset.source == self.nc_path, 'Source mismatch: cache {} vs. dataset {}'.format(cache_dataset.source, self.nc_path)
                
                if 'point' not in cache_dataset.dimensions.keys():
                    cache_dataset.createDimension(dimname='point', size=line_index.shape[0]) 
                    
                if 'line' not in cache_dataset.dimensions.keys():
                    cache_dataset.createDimension(dimname='line', size=line.shape[0]) 
                    
                if 'line' not in cache_dataset.variables.keys():
                    cache_dataset.createVariable('line',
                                                 line.dtype,
                                                 dimensions=['line'],
                                                 **self.CACHE_VARIABLE_PARAMETERS
                                                 )
                cache_dataset.variables['line'][:] = line # Write lines to cache file
                
                if 'line_index' not in cache_dataset.variables.keys():
                    cache_dataset.createVariable('line_index',
                                                 line_index.dtype,
                                                 dimensions=['point'],
                                                 **self.CACHE_VARIABLE_PARAMETERS
                                                 )
                cache_dataset.variables['line_index'][:] = line_index # Write lines to cache file
                
                cache_dataset.close() 
                logger.debug('Saved {} lines for {} points to cache file {}'.format(line.shape[0], line_index.shape[0], self.cache_path))

        return line, line_index
        
    @property
    def line(self):
        '''
        Property getter function to return array of all line numbers
        Always cache this in memory - should only be small
        The order of priority for retrieval is memory, memcached, disk cache then dataset.
        '''
        line = None
        line_index = None
        if self.enable_memory_cache and self._line is not None:
            #logger.debug('Returning memory cached line')
            return self._line

        elif self.memcached_connection is not None:
            line_cache_key = self.cache_basename + '_line'
            # line = self.memcached_connection.get(line_cache_key)
            # if line is not None:
            #     logger.debug('memcached key found at {}'.format(line_cache_key))
            # else:
            #     line = self.get_line_index_values()
            #     logger.debug('Memcached key not found. Adding value with key {}'.format(line_cache_key))
            #     self.memcached_connection.add(line_cache_key, line)
            line = self.memcached_connection.get(line_cache_key)
            if line is not None:    
                logger.debug('memcached key found at {}'.format(line_cache_key))
            else:
                line = self.get_line_values()
                logger.debug('memcached key not found. Adding entry with key {}'.format(line_cache_key))
                self.memcached_connection.add(line_cache_key, line)
            
        elif self.enable_disk_cache:
            line, line_index = self.get_cached_line_arrays()           
        else: # No caching - read line from source file
            line = self.get_line_values()
            line_index = None

        if self.enable_memory_cache:
            self._line = line
            if line_index is not None:
                self._line_index = line_index
                
        #logger.debug('line: {}'.format(line))
        return line

    @property
    def line_index(self):
        '''
        Property getter function to return line_indices for all points
        The order of priority for retrieval is memory, memcached, disk cache then dataset.
        '''
        line = None
        line_index = None
        if self.enable_memory_cache and self._line_index is not None:
            #logger.debug('Returning memory cached line_index')
            return self._line_index

        elif self.memcached_connection is not None:
            line_index_cache_key = self.cache_basename + '_line_index'

            # line_index = self.memcached_connection.get(line_index_cache_key)
            # if line_index is not None:
            #     logger.debug('memcached key found at {}'.format(line_index_cache_key))
            # else:
            #     line_index = self.get_line_index_values()
            #     logger.debug('Memcached key not found. Adding value with key {}'.format(line_index_cache_key))
            #     self.memcached_connection.add(line_index_cache_key, line_index)

            line_index = self.memcached_connection.get(line_index_cache_key)
            if line_index is not None:
                logger.debug('memcached key found at {}'.format(line_index_cache_key))
            else:
                line_index = self.get_line_index_values()
                logger.debug('memcached key not found. Adding entry with key {}'.format(line_index_cache_key))
                self.memcached_connection.add(line_index_cache_key, line_index)

        elif self.enable_disk_cache:
            line, line_index = self.get_cached_line_arrays()           
        else:  # No caching - read line_index from source file
            line = None
            line_index = self.get_line_index_values()

        if self.enable_memory_cache:
            if line is not None:
                self._line = line
            self._line_index = line_index
            
        #logger.debug('line_index: {}'.format(line_index))
        return line_index

    def get_line_start_end_points(self):
        '''\
        Function to return n x 2 array of coordinates for line start & end points
        '''
        return self.get_line_sample_points(line_divisions=1)

    def get_line_sample_points(self, line_divisions=10):
        '''\
        Function to return n x 2 array of coordinates for line start, division points & end points
        @param line_divisions: Number of sampling subdivisions for each line (1 = start/end points only)
        '''    
        line_sample_indices_set = set()
        for line_index in range(self.netcdf_dataset.dimensions['line'].size):
            line_indices = np.where(self.netcdf_dataset.variables['line_index'][:] == line_index)[0]
            logger.debug('Sampling line index {} with {} points'.format(line_index, len(line_indices)))
            valid_coord_mask = ~np.any(np.isnan(self.xycoords[line_indices]), axis=1) 
            if not np.count_nonzero(valid_coord_mask): # No valid coordinates in line
                logger.debug('No valid coordinates found in line index {}'.format(line_index))
                continue
            
            #logger.debug('Found {}/{} valid points in line index {}'.format(np.count_nonzero(valid_coord_mask), len(line_indices), line_index))
            line_indices = line_indices[valid_coord_mask] # Filter out NaN ordinates  
                       
            # Take samples between first and last valid line indices            
            sampling_increment = max(1, int(len(line_indices)/line_divisions))
            line_sample_indices_set |= set([line_indices[sample_index]
                                            for sample_index in range(0, len(line_indices), sampling_increment)])
            line_sample_indices_set.add(line_indices[-1]) # Make sure last point is included
        line_sample_indices = np.array(sorted(list(line_sample_indices_set)))
        
        return self.xycoords[line_sample_indices]

    def get_convex_hull(self, to_wkt=None):
        '''\
        Function to return n x 2 array of coordinates for convex hull based on line start/end points
        Implements abstract base function in NetCDFUtils 
        @param to_wkt: CRS WKT for shape
        '''
        points = transform_coords(self.get_line_sample_points(), self.wkt, to_wkt)
        
        try:
            convex_hull = points2convex_hull(points)
        except:
            #logger.info('Unable to compute convex hull. Using rectangular bounding box instead.')
            convex_hull = self.native_bbox
            
        return convex_hull
    
    def get_multi_line_string(self, to_wkt=None, tolerance=0):
        '''\
        Function to return a shapely MultiLineString object representing the line dataset
        '''
        _line_indices, line_start_indices = np.sort(np.unique(self.line_index, return_index=True))
        
        line_end_indices = np.array(line_start_indices)
        line_end_indices[0:-1] = line_start_indices[1:]
        line_end_indices[-1] = self.point_count
        line_end_indices = line_end_indices -1
        
        line_list = []
        for line_index in range(len(line_start_indices)):
            line_slice = slice(line_start_indices[line_index], line_end_indices[line_index]+1)
            line_vertices = self.xycoords[line_slice]
            line_vertices = line_vertices[~np.any(np.isnan(line_vertices), axis=1)] # Discard null coordinates
            if len(line_vertices) >= 2: # LineStrings must have at least 2 coordinate tuples
                line_list.append(LineString(transform_coords(line_vertices, self.wkt, to_wkt)).simplify(tolerance))
        
        return MultiLineString(line_list)

        

    def get_concave_hull(self, to_wkt=None, buffer_distance=0.02, offset=0.0005, tolerance=0.0005, cap_style=1, join_style=1, max_polygons=5, max_vertices=1000):
        """\
        Returns the concave hull (as a shapely polygon) of points with data. 
        Implements abstract base function in NetCDFUtils 
        @param to_wkt: CRS WKT for shape
        @param buffer_distance: distance to buffer (kerf) initial shape outwards then inwards to simplify it
        @param offset: Final offset of final shape from original lines
        @param tolerance: tolerance for simplification
        @param cap_style: cap_style for buffering. Defaults to round
        @param join_style: join_style for buffering. Defaults to round
        @param max_polygons: Maximum number of polygons to accept. Will keep doubling buffer_distance until under this limit. 0=unlimited.
        @param max_vertices: Maximum number of vertices to accept. Will keep doubling buffer_distance until under this limit. 0=unlimited.
        @return shapely.geometry.shape: Geometry of concave hull
        """
        assert not max_polygons or buffer_distance > 0, 'buffer_distance must be greater than zero if number of polygons is limited' # Avoid endless recursion
        
        def get_offset_geometry(geometry, buffer_distance, offset, tolerance, cap_style, join_style, max_polygons, max_vertices):
            '''\
            Helper function to return offset geometry. Will keep trying larger buffer_distance values until there is a manageable number of polygons
            '''
            logger.debug('Computing offset geometry with buffer_distance = {}'.format(buffer_distance))
            offset_geometry = geometry.buffer(buffer_distance, cap_style=cap_style, join_style=join_style).simplify(tolerance)
            offset_geometry = offset_geometry.buffer(offset-buffer_distance, cap_style=cap_style, join_style=join_style).simplify(tolerance)
        
            # Discard any internal polygons
            if type(offset_geometry) == MultiPolygon:
                polygon_list = []
                for polygon in offset_geometry:
                    polygon = Polygon(polygon.exterior)
                    polygon_is_contained = False
                    for list_polygon in polygon_list:
                        polygon_is_contained = list_polygon.contains(polygon)
                        if polygon_is_contained:
                            break
                        elif polygon.contains(list_polygon):
                            polygon_list.remove(list_polygon)
                            break
                        
                    if not polygon_is_contained:
                        polygon_list.append(polygon)       

                if len(polygon_list) == 1:
                    offset_geometry = polygon_list[0] # Single polygon
                else:
                    offset_geometry = MultiPolygon(polygon_list)
                    
            elif type(offset_geometry) == Polygon:
                offset_geometry = Polygon(offset_geometry.exterior)
            else:
                raise ValueError('Unexpected type of geometry: {}'.format(type(offset_geometry)))
            
            # Keep doubling the buffer distance if there are too many polygons
            if (
                (max_polygons and type(offset_geometry) == MultiPolygon and len(offset_geometry) > max_polygons)
                or
                (max_vertices and type(offset_geometry) == MultiPolygon and 
                    sum([len(polygon.exterior.coords) #+ sum([len(interior_ring.coords) for interior_ring in polygon.interiors]) 
                         for polygon in offset_geometry]) > max_vertices)
                or
                (max_vertices and type(offset_geometry) == Polygon and 
                    (len(offset_geometry.exterior.coords) #+ sum([len(interior_ring.coords) for interior_ring in offset_geometry.interiors])
                     ) > max_vertices)
                ):
                return get_offset_geometry(geometry, buffer_distance*2, offset, tolerance, cap_style, join_style, max_polygons, max_vertices)
                
            return offset_geometry
                    
                    
        multi_line_string = self.get_multi_line_string(to_wkt=to_wkt, tolerance=tolerance)
        
        return get_offset_geometry(multi_line_string, buffer_distance, offset, tolerance, cap_style, join_style, max_polygons, max_vertices)

    def set_global_attributes(self, 
                              compute_shape=False,
                              buffer_distance=0.02,
                              offset=0.0005,
                              tolerance=0.0005,
                              max_polygons=5,
                              max_vertices=1000,
                              compute_median_sample_spacing=False,
                              clockwise_polygon_orient=False,
                              shape_ordinate_decimal_place=6):
        '''\
        Function to set  global geometric metadata attributes in netCDF file
        N.B: This will fail if dataset is not writable
        '''
        try:
            super().set_global_attributes(compute_shape=False)  # Call NetCDFPointUtils.set_global_attributes


            attribute_dict = {}
            if compute_shape:
                print("compute shape in lines_utils")
                wkt_polygon = shapely.wkt.dumps(asPolygon(self.get_concave_hull()),
                                                rounding_precision=shape_ordinate_decimal_place)

                # get wkt polygon as polygon object to set as either clockwise or anticlockwise.
                pol = shapely.wkt.loads(wkt_polygon)
                if (clockwise_polygon_orient):
                    logger.debug("setting 'geospatial_bounds' as a clockwise orientation")
                    wkt_polygon = shapely.geometry.polygon.orient(Polygon(pol), -1.0)
                else:  # reverse polygon coordinates - anti-clockwise
                    logger.debug("Keeping default setting 'geospatial_bounds' to anticlockwise orientation")
                    wkt_polygon = shapely.geometry.polygon.orient(Polygon(pol), 1.0)

                attribute_dict['geospatial_bounds'] = str(wkt_polygon)
                logger.debug(attribute_dict['geospatial_bounds'])


            if compute_median_sample_spacing:
                try:
                    logger.debug('Computing median sample spacing in metres')
                    _utm_wkt, utm_coordinate_array = utm_coords(self.xycoords[::POINT_STRIDE], self.wkt)
                    #logger.debug('utm_coordinate_array = {}'.format(utm_coordinate_array))        
                    sample_distance_array = coords2distance(utm_coordinate_array) # Absolute distances of sample points along line
                    sample_distance_array = sample_distance_array[1:] - sample_distance_array[:-1] # Compute relative distances from absolute distances
                    #logger.debug('sample_distance_array = {}'.format(sample_distance_array))        
                    median_sample_spacing_m = round(np.nanmedian(sample_distance_array) / POINT_STRIDE, 1)
                    assert not np.isnan(median_sample_spacing_m), 'median_sample_spacing_m is Not a Number (NaN)'
                    assert not np.isinf(median_sample_spacing_m), 'median_sample_spacing_m is infinite'
                    attribute_dict['median_sample_spacing_m'] = median_sample_spacing_m
                except Exception as e:
                    logger.warning('Unable to compute median sample spacing : {}'.format(e))
                    try:
                        del self.netcdf_dataset.median_sample_spacing_m # Delete any existing value
                    except:
                        pass
        
            logger.debug('attribute_dict = {}'.format(pformat(attribute_dict)))
        
            logger.debug('Writing global attributes to netCDF dataset')
            for key, value in attribute_dict.items():
                setattr(self.netcdf_dataset, key, value)
                
            logger.debug('Finished setting global geometric metadata attributes in netCDF line dataset')
        except:
            logger.error('Unable to set geometric metadata attributes in netCDF line dataset')
            raise



    def fix_missing_coordinates(self):
        '''\
        Function to interpolate or extrapolate missing coordinates in netCDF file
        '''
        INVALID_COORDINATE_FLAG = 0
        OBSERVED_COORDINATE_FLAG = 1
        INTERPOLATED_COORDINATE_FLAG = 2
        EXTRAPOLATED_COORDINATE_FLAG = 3
        
        COORDINATE_FLAG_LIST = ['Invalid', 'Observed', 'Interpolated', 'Extrapolated']
    
        #TODO: Fix up questionable scoping of variables by passing them explicitly
        def interpolate(last_good_coord_index, next_good_coord_index, bad_point_count):
            '''\
            Function to interpolate or extrapolate missing points
            '''
            def set_missing_ordinates(interpolated_point_array, start_index, flag_index_value):
                '''\
                Helper function to set ordinates
                '''
                update_slice = slice(start_index, start_index+len(interpolated_point_array))
                assert np.all(np.any(np.isnan(self.xycoords[update_slice]), axis=1)), 'Unable to overwrite good coordinates'
                self.xycoords[update_slice] = interpolated_point_array #TODO: Check this - it could be dangerous setting a property
                self.netcdf_dataset.variables['longitude'][update_slice] = interpolated_point_array[:,0]
                self.netcdf_dataset.variables['latitude'][update_slice] = interpolated_point_array[:,1]
                
                #Set coordinate flag indices
                self.netcdf_dataset.variables['coordinate_flag_index'][update_slice] = flag_index_value
            
            logger.debug('last_good_coord_index = {}, next_good_coord_index = {}, bad_point_count = {}'.format(last_good_coord_index, next_good_coord_index, bad_point_count))
            if last_good_coord_index is not None:
                logger.debug('self.xycoords[self.xycoords[last_good_coord_index] = {}'.format(self.xycoords[last_good_coord_index]))
            if next_good_coord_index is not None:
                logger.debug('self.xycoords[self.xycoords[next_good_coord_index] = {}'.format(self.xycoords[next_good_coord_index]))
            
            interpolated_point_array = np.ndarray(shape=(bad_point_count,2), dtype=self.xycoords.dtype)
            
            if last_good_coord_index is not None and next_good_coord_index is not None: # Interpolation between points required
                for interpolated_point_index in range(1, bad_point_count+1):
                    interpolated_point_array[interpolated_point_index-1] = (
                        (bad_point_count-interpolated_point_index+1)*self.xycoords[last_good_coord_index]
                        + interpolated_point_index*self.xycoords[next_good_coord_index]) / (bad_point_count+1)
                set_missing_ordinates(self, interpolated_point_array, 
                                      start_index=last_good_coord_index+1, 
                                      flag_index_value=INTERPOLATED_COORDINATE_FLAG)
        
            elif last_good_coord_index is None and next_good_coord_index is not None: # Extrapolation to start of line required
                known_coords = self.xycoords[next_good_coord_index:next_good_coord_index+2]
                assert np.all(~np.isnan(known_coords)), 'Unable to extrapolate to a single point'
                delta = known_coords[0] - known_coords[1] # Note negative delta
                for interpolated_point_index in range(bad_point_count,0,-1):
                    interpolated_point_array[interpolated_point_index-1] = (
                        known_coords[0] + (bad_point_count - interpolated_point_index + 1) * delta)
                set_missing_ordinates(self, interpolated_point_array, 
                                      start_index=next_good_coord_index-bad_point_count, 
                                      flag_index_value=EXTRAPOLATED_COORDINATE_FLAG)
                
            elif last_good_coord_index is not None and next_good_coord_index is None: # Extrapolation from end of line required
                known_coords = self.xycoords[last_good_coord_index-1:last_good_coord_index+1]
                assert np.all(~np.isnan(known_coords)), 'Unable to extrapolate to a single point'
                delta = known_coords[1] - known_coords[0] # Note positive delta
                for interpolated_point_index in range(1, bad_point_count+1):
                    interpolated_point_array[interpolated_point_index-1] = (
                        known_coords[1] + interpolated_point_index * delta)        
                set_missing_ordinates(self, interpolated_point_array, 
                                      start_index=last_good_coord_index+1, 
                                      flag_index_value=EXTRAPOLATED_COORDINATE_FLAG)
            else:
                raise BaseException('Need to provide at least one of last_good_coord_index and/or next_good_coord_index')
                        
            logger.debug('interpolated_point_array:\n{}'.format(interpolated_point_array))
        
        try:
            coordinate_flag_array = np.array(COORDINATE_FLAG_LIST)
    
            # Only ever do this once - skip operation if dimension or variables already exist
            try:
                self.netcdf_dataset.createDimension('coordinate_flag', 
                                                    len(COORDINATE_FLAG_LIST)
                                                    )
        
                coordinate_flag_variable = self.netcdf_dataset.createVariable(
                            'coordinate_flag', 
                            coordinate_flag_array.dtype, 
                            ['coordinate_flag'],
                            **DEFAULT_VAR_OPTIONS
                            )
                
                coordinate_flag_index_variable = self.netcdf_dataset.createVariable(
                            'coordinate_flag_index', 
                            np.ubyte, 
                            ['point'],
                            chunksizes=([min(DEFAULT_CHUNK_SPEC['point'], self.point_count)] 
                                if DEFAULT_CHUNK_SPEC and DEFAULT_CHUNK_SPEC.get('point') 
                                else None)
                            **DEFAULT_VAR_OPTIONS,
                            )
                coordinate_flag_index_variable.lookup = 'coordinate_flag'
                
            except RuntimeError:
                logger.info('Missing coordinates already interpolated and/or extrapolated (coordinate flag variables already exist)')    
                return
                
            coordinate_flag_variable[:] = coordinate_flag_array[:] # Set flag values
            
            bad_coord_mask = np.any(np.isnan(self.xycoords), axis=1)
            
            _line_indices, line_start_indices = np.sort(np.unique(self.line_index, return_index=True))
            
            line_end_indices = np.array(line_start_indices)
            line_end_indices[0:-1] = line_start_indices[1:]
            line_end_indices[-1] = len(self.line_index)
            line_end_indices = line_end_indices - 1
            
            bad_coord_start_mask = np.ones(shape=bad_coord_mask.shape, dtype=np.bool) # Make first element True
            bad_coord_start_mask[1:] = ~bad_coord_mask[0:-1] # Copy all cells shifted rightward
            bad_coord_start_mask[:] = np.logical_and(bad_coord_mask, bad_coord_start_mask)
            bad_coord_start_indices = np.where(bad_coord_start_mask)[0]
            
            bad_coord_end_mask = np.ones(shape=bad_coord_mask.shape, dtype=np.bool) # Make last element True
            bad_coord_end_mask[0:-1] = ~bad_coord_mask[1:] # Copy inverted cells shifted leftward
            bad_coord_end_mask[:] = np.logical_and(bad_coord_mask, bad_coord_end_mask)
            bad_coord_end_indices = np.where(bad_coord_end_mask)[0]
            
            assert bad_coord_start_indices.shape == bad_coord_end_indices.shape, 'Every bad coordinate range needs a start and end index'
            logger.debug('{} invalid coordinates found in {} blocks'.format(np.count_nonzero(bad_coord_mask), bad_coord_start_indices.shape[0]))
            
            coordinate_flag_index_variable[:] = OBSERVED_COORDINATE_FLAG # Default to "Observed"
            coordinate_flag_index_variable[bad_coord_mask] = INVALID_COORDINATE_FLAG # Set bad coordinates to "Invalid"
        
            for bad_range_index in range(len(bad_coord_start_indices)):
                bad_coord_start_index = bad_coord_start_indices[bad_range_index]
                bad_coord_end_index = bad_coord_end_indices[bad_range_index]
                try:
                    # Find first line start index in bad range (if any)
                    bad_line_start_index = line_start_indices[
                        np.where(np.logical_and((line_start_indices >= bad_coord_start_index),
                                                (line_start_indices <= bad_coord_end_index)))[0][0]
                        ]
                except IndexError:
                    bad_line_start_index = None
                    
                try:
                    # Find last line end index in bad range (if any)
                    bad_line_end_index = line_end_indices[
                        np.where(np.logical_and((line_end_indices >= bad_coord_start_index),
                                                (line_end_indices <= bad_coord_end_index)))[0][-1]
                        ]
                except IndexError:
                    bad_line_end_index = None
                
                logger.debug('bad_coord_start_index = {}, bad_line_end_index = {}, bad_line_start_index = {}, bad_coord_end_index = {}'.format(
                    bad_coord_start_index, 
                    bad_line_end_index, 
                    bad_line_start_index, 
                    bad_coord_end_index
                    )
                )
                
                logger.debug('Extended missing coordinate range:\n{}'.format(self.xycoords[bad_coord_start_index-1:bad_coord_end_index+2]))
                
                logger.debug('Extended Line numbers for missing points:\n{}'.format(
                    self.netcdf_dataset.variables['line'][:][(self.netcdf_dataset.variables['line_index'][bad_coord_start_index-1:bad_coord_end_index+2])]))
                    
                if (bad_line_end_index is not None
                    and bad_line_start_index is not None
                    # Detect when a single line starts and finishes in the bad range (can't extrapolate ends)
                    and (self.netcdf_dataset.variables['line_index'][bad_coord_start_index] ==
                         self.netcdf_dataset.variables['line_index'][bad_coord_end_index])
                    ):
                    logger.debug('Unable to interpolate entire line(s)')
                    continue
                
                if bad_line_start_index is not None: # Coordinates need to be extrapolated to start of line
                    logger.debug('Coordinates need to be extrapolated to start of line')
                    interpolate(self, 
                                last_good_coord_index=None, 
                                next_good_coord_index=bad_coord_end_index+1, 
                                bad_point_count=bad_coord_end_index-bad_line_start_index+1
                                )
                    
                if bad_line_end_index is not None: # Coordinates need to be extrapolated from end of line
                    logger.debug('Coordinates need to be extrapolated from end of line')
                    interpolate(self, 
                                last_good_coord_index=bad_coord_start_index-1, 
                                next_good_coord_index=None, 
                                bad_point_count=bad_line_end_index-bad_coord_start_index+1
                                )
                    
                if bad_line_start_index is None and bad_line_end_index is None: # Coordinates need to be interpolated between good points within a line
                    logger.debug('Coordinates need to be interpolated between good points within a line')
                    interpolate(self, 
                                last_good_coord_index=bad_coord_start_index-1, 
                                next_good_coord_index=bad_coord_end_index+1, 
                                bad_point_count=bad_coord_end_index-bad_coord_start_index+1
                                )
            
            logger.info('Finished interpolating and extrapolating missing coordinates in netCDF line dataset')
            
            for flag_index in range(len(COORDINATE_FLAG_LIST)):
                logger.debug('{} coordinate count = {}'.format(COORDINATE_FLAG_LIST[flag_index], np.count_nonzero(coordinate_flag_index_variable[:] == flag_index)))
            logger.debug('Total coordinate count = {}'.format(self.point_count))
        except:
            logger.error('Unable to interpolate and extrapolate missing coordinates in netCDF line dataset')
            raise
        
if __name__ == '__main__':
    # Setup logging handlers if required
    if not logger.handlers:
        # Set handler for root logger to standard output
        console_handler = logging.StreamHandler(sys.stdout)
        #console_handler.setLevel(logging.INFO)
        console_handler.setLevel(logging.DEBUG)
        console_formatter = logging.Formatter('%(message)s')
        console_handler.setFormatter(console_formatter)
        
        logging.getLogger().addHandler(console_handler) # Add handler to root logger

    nclu = NetCDFLineUtils('C:\\Users\\alex\\Documents\\GADDS2\\P544MAG.nc', debug=False)
    print('{} points in {} lines'.format(nclu.point_count, nclu.netcdf_dataset.dimensions['line'].size))
    #sample_points = nclu.get_line_sample_points(line_divisions=3)
    #print(len(sample_points), sample_points) 

    concave_hull = nclu.get_concave_hull(to_wkt='GDA94')
    print('Shape has {} vertices'.format(len(concave_hull.exterior.coords)))
