"""Ash-Prio ohne Factory: welche Handtrap welchen Effekt trifft (aus dem Kartentext)."""

import unittest

from handtrap_targets import card_targets, targets

LUKIAS = ("Effect Monster", "effect", "Wyrm",
          'If this card is Normal or Special Summoned: You can add 1 "Dracotail" monster from your Deck to your hand. '
          'You can only use this effect of "Dracotail Lukias" once per turn.')
BRANDED_FUSION = ("Spell Card", "spell", "Normal",
                  'Fusion Summon 1 Fusion Monster that mentions "Fallen of Albaz" as material from your Extra Deck, '
                  'using 2 monsters from your hand, Deck, or field as material. You cannot Special Summon from the '
                  'Extra Deck, except Fusion Monsters, the turn you activate this card. You can only activate 1 '
                  '"Branded Fusion" per turn.')
ASH_BLOSSOM = ("Tuner Effect Monster", "effect", "Zombie",
               "When a card or effect is activated that includes any of these effects (Quick Effect): You can discard "
               "this card; negate that effect. You can only use this effect of \"Ash Blossom & Joyous Spring\" once "
               "per turn.")
SUPER_POLY_LIKE = ("Spell Card", "spell", "Quick-Play",
                   "Fusion Summon 1 Fusion Monster from your Extra Deck, using monsters from your field as material.")
FIELD_SPELL = ("Spell Card", "spell", "Field",
               'You can target 1 monster you control; Special Summon 1 "X" monster from your hand.')
GY_EFFECT = ("Effect Monster", "effect", "Dragon",
             "If this card is in your GY: You can banish it; draw 1 card.")


class TargetsTest(unittest.TestCase):
    def test_search_monster_is_hit_by_ash_droll_imperm_and_ogre(self):
        hits = targets(*LUKIAS)
        self.assertEqual(sorted(hits), ["ash", "droll", "imperm", "ogre"])
        self.assertTrue(hits["ash"].startswith('You can add 1 "Dracotail" monster from your Deck'))

    def test_material_from_deck_counts_for_ash_but_not_extra_deck_or_field(self):
        self.assertEqual(sorted(targets(*BRANDED_FUSION)), ["ash"])  # Normalzauber: kein Imperm/Ogre
        self.assertEqual(targets(*SUPER_POLY_LIKE), {})

    def test_restrictions_and_hand_effects_are_not_targets(self):
        self.assertEqual(targets(*ASH_BLOSSOM), {})  # aus der Hand, Rest ist Einschränkung

    def test_face_up_field_spell_is_hit_by_ogre_only(self):
        self.assertEqual(sorted(targets(*FIELD_SPELL)), ["ogre"])

    def test_graveyard_effect_is_not_on_the_field(self):
        self.assertEqual(targets(*GY_EFFECT), {})

    def test_card_info(self):
        self.assertEqual(card_targets(None), {})
