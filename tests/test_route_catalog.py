"""Full metro coverage, branch service order, historical openings and folder aliases."""
import unittest

from metro_braking.routes import CATALOG, route_from_name, display_recording_name


class RouteCatalogTests(unittest.TestCase):
    def route(self,line,origin,destination,date='2024-07-01'):
        return route_from_name(f'L{line}_{origin}_-_{destination}-{date}_17-04-03')

    def test_all_sixteen_lines_have_complete_station_lists(self):
        expected={'1':25,'2':25,'3':25,'3bis':4,'4':29,'5':22,'6':28,'7':38,
                  '7bis':8,'8':38,'9':37,'10':23,'11':19,'12':31,'13':32,'14':21}
        self.assertEqual(set(CATALOG['lines']),set(expected))
        for line,count in expected.items():
            with self.subTest(line=line):
                item=CATALOG['lines'][line]
                self.assertEqual(len(item['stations']),count)
                self.assertEqual(len(set(item['stations'])),count)
                services=item.get('paths',[{'stations':item['stations']}])
                self.assertEqual(set(item['stations']),{station for path in services for station in path['stations']})
                # Every station is reachable within at least one of its declared services.
                for service in services:
                    sequence=service['stations']
                    for i in range(len(sequence)-1):
                        result=self.route(line,sequence[i],sequence[i+1],date='2026-10-15')
                        self.assertEqual(result['route_status'],'resolved')
                        self.assertEqual(result['expected_intermediate_stops'],0)

    def test_user_sentier_saint_lazare_example_and_display(self):
        name='L3_Sentier_-_St-Lazare_-2024-05-03_17-04-03'
        result=route_from_name(name)
        self.assertEqual(result['route_status'],'resolved')
        self.assertEqual(result['expected_intermediate_stops'],4)
        self.assertEqual(result['route_stations'].split(' → '),['Sentier','Bourse','Quatre-Septembre','Opéra','Havre - Caumartin','Saint-Lazare'])
        self.assertEqual(display_recording_name(name),'L3 Sentier – Saint-Lazare 17h04, 03/05/2024')
        reverse=self.route('3','St_Lazare','Sentier')
        self.assertEqual(reverse['route_stations'].split(' → '),list(reversed(result['route_stations'].split(' → '))))

    def test_bis_prefixes_abbreviations_and_metadata(self):
        for prefix in ('3bis','3_bis','3-bis','3b','3B','3 BIS'):
            with self.subTest(prefix=prefix):
                name=f'L{prefix}_Gambetta_-_Porte_des_Lilas_bag_Yinv-2024-05-03_17-04-03'
                route=route_from_name(name)
                self.assertEqual(route['line'],'3bis')
                self.assertEqual(route['expected_intermediate_stops'],2)
                self.assertTrue(display_recording_name(name).endswith('(bag Yinv)'))
        self.assertEqual(route_from_name('L3 Sentier - St-Lazare -2024-05-03_17-04-03')['expected_intermediate_stops'],4)

    def test_both_branches_are_supported_without_counting_the_other_branch(self):
        cases=[('7','La_Courneuve','Mairie_d_Ivry',32,'Porte de Choisy','Le Kremlin-Bicêtre'),
               ('7','La_Courneuve','Villejuif_Louis_Aragon',31,'Le Kremlin-Bicêtre','Porte de Choisy'),
               ('13','Les_Courtilles','Chatillon_Montrouge',22,'Les Agnettes','Saint-Denis - Université'),
               ('13','Saint_Denis_Universite','Chatillon_Montrouge',24,'Carrefour Pleyel','Les Agnettes')]
        for line,start,end,count,present,absent in cases:
            with self.subTest(line=line,start=start):
                route=self.route(line,start,end)
                self.assertEqual(route['route_status'],'resolved')
                self.assertEqual(route['expected_intermediate_stops'],count)
                self.assertIn(present,route['route_stations'])
                self.assertNotIn(absent,route['route_stations'])
        self.assertEqual(self.route('13','Les_Courtilles','Saint_Denis_Universite')['route_status'],'no direct regular service between endpoints')

    def test_directional_loops_do_not_reverse_a_station_list(self):
        east=self.route('10','Boulogne_Jean_Jaures','Javel_Andre_Citroen')['route_stations'].split(' → ')
        west=self.route('10','Javel_Andre_Citroen','Boulogne_Jean_Jaures')['route_stations'].split(' → ')
        self.assertEqual(east,['Boulogne - Jean Jaurès','Michel-Ange - Molitor','Chardon-Lagache','Mirabeau','Javel - André Citroën'])
        self.assertEqual(west,['Javel - André Citroën',"Église d'Auteuil",'Michel-Ange - Auteuil',"Porte d'Auteuil",'Boulogne - Jean Jaurès'])
        outbound=self.route('7bis','Louis_Blanc','Pre_Saint_Gervais')
        inbound=self.route('7bis','Pre_Saint_Gervais','Louis_Blanc')
        self.assertEqual(outbound['expected_intermediate_stops'],5)
        self.assertEqual(inbound['expected_intermediate_stops'],5)
        self.assertIn('Place des Fêtes',outbound['route_stations']);self.assertNotIn('Danube',outbound['route_stations'])
        self.assertIn('Danube',inbound['route_stations']);self.assertNotIn('Place des Fêtes',inbound['route_stations'])

    def test_openings_follow_recording_date(self):
        self.assertEqual(self.route('11','Mairie_des_Lilas','Rosny_Bois_Perrier','2024-06-12')['route_status'],'endpoint not open on recording date')
        self.assertEqual(self.route('11','Mairie_des_Lilas','Rosny_Bois_Perrier','2024-06-13')['expected_intermediate_stops'],5)
        self.assertEqual(self.route('14','St_Lazare','Orly','2024-06-23')['route_status'],'endpoint not open on recording date')
        old=self.route('14','St_Lazare','Orly','2024-06-24')
        new=self.route('14','St_Lazare','Orly','2025-01-18')
        self.assertEqual(old['route_status'],'resolved')
        self.assertNotIn('Villejuif - Gustave Roussy',old['route_stations'])
        self.assertIn('Villejuif - Gustave Roussy',new['route_stations'])
        self.assertEqual(new['expected_intermediate_stops'],old['expected_intermediate_stops']+1)

    def test_unknown_stations_and_lines_still_fail_explicitly(self):
        self.assertEqual(self.route('3','Not_a_station','Sentier')['route_status'],'unrecognized station name on this line')
        self.assertEqual(self.route('99','A','B')['route_status'],'line outside reference coverage')


if __name__=='__main__':
    unittest.main()
