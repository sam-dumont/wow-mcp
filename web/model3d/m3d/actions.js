/* Character motions for this viewer. Weapon swings and emotes use the
   AnimationData IDs in TrinityCore SharedDefines.h. Spells and ranged
   attacks come from the installed client's spell visual and equipment kits. */
'use strict';

(() => {
	const M3D = globalThis.M3D;
	const READY = { 16: 25, 17: 26, 18: 27, 19: 28 };
	const EMOTES = [
		['roar', 55], ['talk', 60], ['exclaim', 64], ['question', 65],
		['bow', 66], ['cheer', 68], ['laugh', 70]
	];
	M3D.moves = function moves(attack, spells) {
		const swing = READY[attack] ? attack : 16;
		return [
			{ name: 'attack', equipment: 'melee', steps: [[READY[swing], 1, true], [swing, 1], [swing, 1], [READY[swing], 1, true]] },
			...spells.map(spell => ({ name: spell.name, equipment: spell.equipment, spell })),
			...EMOTES.map(([name, id]) => ({ name, steps: [[id, 1]] }))
		];
	};

	M3D.RIDER_ANIMATION = 91;
	M3D.GAITS = Object.freeze({ idle: 0, walk: 4, run: 5 });
	// External sequences requested by the shipper, including weapon and
	// spell clips. Which of them plays is resolved by the client's kits.
	M3D.ANIMATIONS = Object.freeze([0, 4, 5, 9, 10, 14, 15, 16, 17, 18, 19, 25, 26, 27, 28, 29, 46,
		48, 49, 51, 52, 53, 54, 55, 60, 64, 65, 66, 68, 70, 91, 95, 105, 106, 107, 108, 109, 110, 111, 112, 124, 125, 159]);
})();
