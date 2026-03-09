#
# ditto_rest_client.py
# REST client for Ditto Things API
#
import requests
import logging
from typing import List, Dict, Optional
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class DittoRestClient:
    def __init__(self, api_url: str, username: str, password: str):
        """
        Args:
            api_url: Base URL for Ditto API
            username: Ditto username
            password: Ditto password
        """
        self.api_url = api_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth

    def get_thing(self, thing_id: str) -> Optional[Dict]:
        """
        Fetch a single thing by ID.
        
        Args:
            thing_id: The thing ID (e.g., "meteo:11217225")
            
        Returns:
            Dictionary with thing data or None if not found
        """
        url = f"{self.api_url}/api/2/things/{thing_id}"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.warning(f"Thing {thing_id} not found")
                return None
            else:
                logger.error(f"Failed to fetch thing {thing_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching thing {thing_id}: {e}")
            return None

    def search_things(self, namespace: str = None, filter_rql: str = None, limit: int = 200) -> List[Dict]:
        """
        Search for things with optional filtering.
        
        Args:
            namespace: Filter by namespace (e.g., "meteo")
            filter_rql: RQL filter string
            limit: Maximum number of results per page (default 200, max 200)
            
        Returns:
            List of thing dictionaries
        """
        url = f"{self.api_url}/api/2/search/things"
        all_items = []
        cursor = None
        
        while True:
            params = {'option': f'size({limit})'}
            
            if namespace:
                params['namespaces'] = namespace
            if filter_rql:
                params['filter'] = filter_rql
            if cursor:
                params['option'] = f'size({limit}),cursor({cursor})'
                
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    # The API returns items in a wrapper
                    if isinstance(data, dict) and 'items' in data:
                        items = data['items']
                        all_items.extend(items)
                        
                        # Check if there's a cursor for next page
                        cursor = data.get('cursor')
                        if not cursor or len(items) < limit:
                            # No more pages
                            break
                    elif isinstance(data, list):
                        all_items.extend(data)
                        break
                    else:
                        break
                else:
                    logger.error(f"Failed to search things: {response.status_code}")
                    break
            except Exception as e:
                logger.error(f"Error searching things: {e}")
                break
                
        return all_items

    def get_all_meteo_things(self) -> List[Dict]:
        """
        Fetch all meteorological things (namespace: meteo).
        
        Returns:
            List of meteo thing dictionaries
        """
        return self.search_things(namespace="meteo")