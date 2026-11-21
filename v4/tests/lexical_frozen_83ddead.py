"""Outputs of the UNMODIFIED base engine (commit 83ddead), pinned as literal data.

Generated once on 2026-11-21 by running base 83ddead (v4/src untouched) on the corpus of
``lexical_fixture_410.py`` with ``Config(store_backend="sqlite_s1", embed_model="hash",
hash_dim=16, use_dense=False)`` (plus ``recency_only_with_at_time=True`` for "floor"), the
retrieval clock pinned to ``NOW``; identical under PYTHONHASHSEED 0, 1 and 4242.
Row = [id, bm25 rank, bm25 score, entity rank, entity score, fused, recency, C4 anchor].
Never regenerate from a later commit: a difference is a policy change (lex-...-v2).
"""

FROZEN = {'c4': {'before': [['c-old', 1, 3.4677541702369528, None, None, 1.5, 0.9961565872205752, True],
                   ['c-backup', 2, 1.8369017825767524, None, None, 0.4882765374643231, 0.9961565872205752, True],
                   ['c-owner', 3, 1.461004835671262, None, None, 0.2550832922893641, 0.9961565872205752, True],
                   ['c-notes', 4, 1.0498221244986776, None, None, 0.0, 0.9961565872205752, False]],
        'now': [['c-new', 2, 2.2670173711380093, None, None, 0.9551788226615001, 0.21847733942238173, True],
                ['c-backup', 3, 1.8266946747241026, None, None, 0.659805154039571, 0.21431099571326817, True],
                ['c-old', 1, 3.0791992118157236, None, None, 1.5, 0.21431099571326817, True],
                ['c-owner', 4, 1.156870538157963, None, None, 0.21047920704573803, 0.21431099571326817, False],
                ['c-notes', 5, 0.8431026507974045, None, None, 0.0, 0.21431099571326817, False]]},
 'c4_state': {'c-new': None, 'c-old': 'c-new'},
 'context_semantic': {'*': '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                           '[episode|2023-11-14|beta] Pixel is also the codename of the beta budget cat.\n'
                           '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                           '[episode|2023-11-16|alpha] Bruno Keller maintains the SQLite migration scripts.\n'
                           '[episode|2023-11-19|alpha] SQLite migration eight adds the change journal.\n'
                           '[episode|2023-11-15|alpha] The budget review moved to Thursday afternoon.\n'
                           '[episode|2023-11-14|alpha] Alice Martin adopted a grey cat named Pixel.\n'
                           '[episode|2023-11-14|beta] The beta budget for Thursday is confidential.\n'
                           '[episode|2023-11-17|alpha] Alice Martin asked Bruno Keller to review the budget.',
                      'alpha': '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                               '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                               '[episode|2023-11-15|alpha] The budget review moved to Thursday afternoon.\n'
                               '[episode|2023-11-16|alpha] Bruno Keller maintains the SQLite migration scripts.\n'
                               '[episode|2023-11-19|alpha] SQLite migration eight adds the change journal.\n'
                               '[episode|2023-11-14|alpha] Alice Martin adopted a grey cat named Pixel.\n'
                               '[episode|2023-11-17|alpha] Alice Martin asked Bruno Keller to review the budget.',
                      'beta': '[episode|2023-11-14|beta] Pixel is also the codename of the beta budget cat.\n'
                              '[episode|2023-11-14|beta] The beta budget for Thursday is confidential.'},
 'floor': {'alone': [['a2', 1, 3.423465194887534, None, None, 1.5, 0.8122523963562355, True],
                     ['a4', 2, 1.3025584601126488, None, None, 0.0, 0.8506671609508557, True]],
           'with_far_other_project': [['a2', 1, 3.423465194887534, 1, 2.3978952727983707, 2.0, 0.9876377174158573, True],
                                      ['a4', 2, 1.3025584601126488, None, None, 0.0, 0.9903716171572856, True]]},
 'main': {'at_time': [['a2', 1, 1.1046025605913727, None, None, 1.5, 0.8122523963562355, True],
                      ['b1', 2, 1.1046025605913727, None, None, 1.5, 0.7937005259840997, True],
                      ['a4', 3, 0.9952058833388571, None, None, 0.4504814305364496, 0.8506671609508557, True],
                      ['b2', 4, 0.9482499045705757, None, None, 0.0, 0.7937005259840997, False]],
          'context_empty_mmr': {'*': '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                                     '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                                     '[episode|2023-11-14|beta] Pixel is also the codename of the beta budget cat.\n'
                                     '[episode|2023-11-16|alpha] Bruno Keller maintains the SQLite migration scripts.\n'
                                     '[episode|2023-11-19|alpha] SQLite migration eight adds the change journal.\n'
                                     '[episode|2023-11-15|alpha] The budget review moved to Thursday afternoon.\n'
                                     '[episode|2023-11-14|beta] The beta budget for Thursday is confidential.\n'
                                     '[episode|2023-11-14|alpha] Alice Martin adopted a grey cat named Pixel.\n'
                                     '[episode|2023-11-17|alpha] Alice Martin asked Bruno Keller to review the budget.',
                                'alpha': '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                                         '[episode|2023-11-14|alpha] Pixel the cat sleeps on the radiator.\n'
                                         '[episode|2023-11-15|alpha] The budget review moved to Thursday afternoon.\n'
                                         '[episode|2023-11-16|alpha] Bruno Keller maintains the SQLite migration scripts.\n'
                                         '[episode|2023-11-19|alpha] SQLite migration eight adds the change journal.\n'
                                         '[episode|2023-11-17|alpha] Alice Martin asked Bruno Keller to review the budget.\n'
                                         '[episode|2023-11-14|alpha] Alice Martin adopted a grey cat named Pixel.',
                                'beta': '[episode|2023-11-14|beta] Pixel is also the codename of the beta budget cat.\n'
                                        '[episode|2023-11-14|beta] The beta budget for Thursday is confidential.'},
          'multi': {'*': [['b2', 0.11249999999999999], ['tie-a', 0.08095238095238094], ['tie-b', 0.07727272727272727], ['a3', 0.07354497354497354],
                          ['a2', 0.07261904761904761], ['a1', 0.07147826086956521], ['b1', 0.06959247648902822], ['a6', 0.06878787878787879],
                          ['a4', 0.06535326086956522], ['b3', 0.03043478260869565]],
                    'alpha': [['tie-a', 0.08095238095238094], ['tie-b', 0.07727272727272727], ['a3', 0.07561904761904761],
                              ['a2', 0.07454212454212454], ['a1', 0.07391304347826086], ['a6', 0.07138047138047138], ['a4', 0.06959247648902822],
                              ['a5', 0.029166666666666664], ['a7', 0.024999999999999998]],
                    'beta': [['b2', 0.1264069264069264], ['b1', 0.07805383022774326], ['b3', 0.031818181818181815]]},
          'ranking': {'*': {'Alice Martin': [['a1', 1, 3.2543710062245856, None, None, 1.5, 9.688727123829343e-05, True],
                                             ['a4', 2, 3.0932192094926942, None, None, 0.0, 0.00010384120617075866, True]],
                            'Bruno Keller SQLite migration': [['a3', 1, 6.165790228780219, None, None, 1.5, 0.00010146946974399259, True],
                                                              ['a6', 2, 3.4332371989331127, None, None, 0.3933631511485587, 0.00010875228492924067,
                                                               True],
                                                              ['b3', 3, 2.891473723020766, None, None, 0.17395828717781675, 9.688727123829343e-05,
                                                               True],
                                                              ['a4', 4, 2.4619287958044764, None, None, 0.0, 0.00010384120617075866, False]],
                            'What did Zoé Lambert join?': [['a7', 1, 4.262715652848048, 1, 2.5649493574615367, 2.0, 0.0001112942490915792, True]],
                            'Where does Pixel the cat sleep?': [['tie-a', 1, 2.443608524936676, None, None, 1.5, 9.688727123829343e-05, True],
                                                                ['tie-b', 2, 2.443608524936676, None, None, 1.5, 9.688727123829343e-05, True],
                                                                ['b2', 3, 2.1158544840495845, None, None, 1.2712669200014508, 9.688727123829343e-05,
                                                                 True],
                                                                ['a1', 4, 1.8960520116780188, None, None, 1.1178711102632457, 9.688727123829343e-05,
                                                                 False],
                                                                ['a5', 5, 0.7688823890985086, None, None, 0.33124166473268535, 0.00010626837930860897,
                                                                 False],
                                                                ['a3', 7, 0.3265869544108983, None, None, 0.02257240539548149, 0.00010146946974399259,
                                                                 False],
                                                                ['a2', 6, 0.3265869544108983, None, None, 0.02257240539548149, 9.91519038520795e-05,
                                                                 False],
                                                                ['b1', 9, 0.3265869544108983, None, None, 0.02257240539548149, 9.688727123829343e-05,
                                                                 False],
                                                                ['a6', 8, 0.3265869544108983, None, None, 0.02257240539548149, 0.00010875228492924067,
                                                                 False],
                                                                ['a7', 10, 0.30957229397849256, None, None, 0.010698209503647145,
                                                                 0.0001112942490915792, False]],
                            'budget Thursday': [['a2', 1, 2.821221160057929, None, None, 1.5, 9.91519038520795e-05, True],
                                                ['b1', 2, 2.821221160057929, None, None, 1.5, 9.688727123829343e-05, True],
                                                ['a4', 3, 0.9952058833388571, None, None, 0.03760547202530075, 0.00010384120617075866, True],
                                                ['b2', 4, 0.9482499045705757, None, None, 0.0, 9.688727123829343e-05, False]],
                            'chat thon croquettes': [['a5', 1, 5.790700883587158, None, None, 1.5, 0.00010626837930860897, True]],
                            'radiator budget migration cat': [['tie-a', 1, 2.821221160057929, None, None, 1.5, 9.688727123829343e-05, True],
                                                              ['tie-b', 2, 2.821221160057929, None, None, 1.5, 9.688727123829343e-05, True],
                                                              ['b2', 3, 1.8964998091411513, None, None, 0.740377644119477, 9.688727123829343e-05,
                                                               True],
                                                              ['a3', 4, 1.7166185994665564, None, None, 0.5926122787624577, 0.00010146946974399259,
                                                               False],
                                                              ['a6', 5, 1.7166185994665564, None, None, 0.5926122787624577, 0.00010875228492924067,
                                                               False],
                                                              ['a2', 6, 1.1046025605913727, None, None, 0.08986508380894512, 9.91519038520795e-05,
                                                               False],
                                                              ['b1', 7, 1.1046025605913727, None, None, 0.08986508380894512, 9.688727123829343e-05,
                                                               False],
                                                              ['a1', 8, 1.0470545255967425, None, None, 0.04259162799917436, 9.688727123829343e-05,
                                                               False],
                                                              ['a4', 9, 0.9952058833388571, None, None, 0.0, 0.00010384120617075866, False]]},
                      'alpha': {'Alice Martin': [['a1', 1, 2.7405562466298226, None, None, 1.5, 9.688727123829343e-05, True],
                                                 ['a4', 2, 2.6051169202252975, None, None, 0.0, 0.00010384120617075866, True]],
                                'Bruno Keller SQLite migration': [['a3', 1, 5.781701600879163, None, None, 1.5, 0.00010146946974399259, True],
                                                                  ['a6', 2, 2.8908508004395816, None, None, 0.13492504164353122,
                                                                   0.00010875228492924067, True],
                                                                  ['a4', 3, 2.6051169202252975, None, None, 0.0, 0.00010384120617075866, True]],
                                'What did Zoé Lambert join?': [['a7', 1, 3.75040407795155, 1, 2.5649493574615367, 2.0, 0.0001112942490915792, True]],
                                'Where does Pixel the cat sleep?': [['tie-a', 1, 2.3341785394895354, None, None, 1.5, 9.688727123829343e-05, True],
                                                                    ['tie-b', 2, 2.3341785394895354, None, None, 1.5, 9.688727123829343e-05, True],
                                                                    ['a1', 3, 1.8269755609135636, None, None, 1.1313705442739597,
                                                                     9.688727123829343e-05, True],
                                                                    ['a5', 4, 0.7149429373112607, None, None, 0.32315766283734965,
                                                                     0.00010626837930860897, False],
                                                                    ['a3', 6, 0.2999528717506719, None, None, 0.021547521609837746,
                                                                     0.00010146946974399259, False],
                                                                    ['a2', 5, 0.2999528717506719, None, None, 0.021547521609837746,
                                                                     9.91519038520795e-05, False],
                                                                    ['a6', 7, 0.2999528717506719, None, None, 0.021547521609837746,
                                                                     0.00010875228492924067, False],
                                                                    ['a7', 8, 0.2843584028085639, None, None, 0.010213635885022616,
                                                                     0.0001112942490915792, False],
                                                                    ['a4', 9, 0.27030530297482763, None, None, 0.0, 0.00010384120617075866, False]],
                                'budget Thursday': [['a2', 1, 3.423465194887534, None, None, 1.5, 9.91519038520795e-05, True],
                                                    ['a4', 2, 1.3025584601126488, None, None, 0.0, 0.00010384120617075866, True]],
                                'chat thon croquettes': [['a5', 1, 5.0957525175423095, None, None, 1.5, 0.00010626837930860897, True]],
                                'radiator budget migration cat': [['tie-a', 1, 2.540026667469597, None, None, 1.5, 9.688727123829343e-05, True],
                                                                  ['tie-b', 2, 2.540026667469597, None, None, 1.5, 9.688727123829343e-05, True],
                                                                  ['a2', 3, 1.4454254002197908, None, None, 0.4070988679847266, 9.91519038520795e-05,
                                                                   True],
                                                                  ['a3', 4, 1.4454254002197908, None, None, 0.4070988679847266,
                                                                   0.00010146946974399259, False],
                                                                  ['a6', 5, 1.4454254002197908, None, None, 0.4070988679847266,
                                                                   0.00010875228492924067, False],
                                                                  ['a4', 6, 1.3025584601126488, None, None, 0.2644538288802541,
                                                                   0.00010384120617075866, False],
                                                                  ['a1', 7, 1.0376932424441359, None, None, 0.0, 9.688727123829343e-05, False]]},
                      'beta': {'Alice Martin': [],
                               'Bruno Keller SQLite migration': [['b3', 1, 2.153143609648302, None, None, 1.5, 9.688727123829343e-05, True]],
                               'What did Zoé Lambert join?': [],
                               'Where does Pixel the cat sleep?': [['b2', 1, 2.3397612874079403, None, None, 1.5, 9.688727123829343e-05, True],
                                                                   ['b1', 2, 0.48733982868512754, None, None, 0.0, 9.688727123829343e-05, True]],
                               'budget Thursday': [['b1', 1, 1.5043472098817126, None, None, 1.5, 9.688727123829343e-05, True],
                                                   ['b2', 2, 0.41796456309023244, None, None, 0.0, 9.688727123829343e-05, True]],
                               'chat thon croquettes': [],
                               'radiator budget migration cat': [['b2', 1, 1.2901958495997818, None, None, 1.5, 9.688727123829343e-05, True],
                                                                 ['b1', 2, 0.48733982868512754, None, None, 0.0, 9.688727123829343e-05, True]]}}}}
